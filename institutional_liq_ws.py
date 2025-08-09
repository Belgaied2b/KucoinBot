import asyncio
import json
import math
import time
from collections import defaultdict, deque
from typing import Callable, Dict, Deque, Tuple, Optional

# Dépendance suggérée: websockets (pip install websockets)
try:
    import websockets
except Exception as _e:
    websockets = None

# ---- SETTINGS (utilise config.SETTINGS si dispo, sinon défauts sûrs) ----
try:
    from config import SETTINGS
    USE_SETTINGS = True
except Exception:
    USE_SETTINGS = False

def _get(key: str, default):
    if USE_SETTINGS and hasattr(SETTINGS, key):
        return getattr(SETTINGS, key)
    return default

WS_HOST = _get("binance_futures_ws_host", "wss://fstream.binance.com")
WINDOW_SEC = int(_get("liq_ws_window_sec", 300))         # fenêtre glissante 5 min
EMIT_EVERY_SEC = int(_get("liq_ws_emit_every_sec", 5))   # fréquence d’émission
NOTIONAL_NORM = float(_get("liq_notional_norm", 150_000.0))
IMB_WEIGHT = float(_get("liq_imbal_weight", 0.35))
BASE_WEIGHT = 1.0 - IMB_WEIGHT
USE_SHOCK = bool(_get("liq_use_shock_boost", True))
SHOCK_EMA_ALPHA = float(_get("liq_shock_ema_alpha", 0.2))
SHOCK_BOOST_MAX = float(_get("liq_shock_boost_max", 0.25))  # up to +0.25 au score
RECONNECT_BASE_WAIT = float(_get("ws_reconnect_base_wait", 2.0))
RECONNECT_MAX_WAIT = float(_get("ws_reconnect_max_wait", 30.0))


class RollingWindow:
    """
    Stocke un flux (t, buy_notional, sell_notional) et purge > WINDOW_SEC.
    Garde un EMA du total pour booster les chocs (optionnel).
    """
    def __init__(self, window_sec: int, ema_alpha: float):
        self.window_sec = window_sec
        self.items: Deque[Tuple[float, float, float]] = deque()
        self.buy_sum = 0.0
        self.sell_sum = 0.0
        self.ema_total: Optional[float] = None
        self.ema_alpha = ema_alpha

    def add(self, ts: float, buy_not: float, sell_not: float):
        self.items.append((ts, buy_not, sell_not))
        self.buy_sum += buy_not
        self.sell_sum += sell_not
        self._purge(ts)

        total = self.buy_sum + self.sell_sum
        if self.ema_total is None:
            self.ema_total = total
        else:
            self.ema_total = self.ema_alpha * total + (1 - self.ema_alpha) * self.ema_total

    def _purge(self, now_ts: float):
        threshold = now_ts - self.window_sec
        while self.items and self.items[0][0] < threshold:
            _, b, s = self.items.popleft()
            self.buy_sum -= b
            self.sell_sum -= s
            if self.buy_sum < 0: self.buy_sum = 0.0
            if self.sell_sum < 0: self.sell_sum = 0.0

    def snapshot(self, now_ts: float) -> Tuple[float, float, float, float]:
        self._purge(now_ts)
        buy = max(0.0, self.buy_sum)
        sell = max(0.0, self.sell_sum)
        total = buy + sell
        imb = (abs(buy - sell) / total) if total > 0 else 0.0
        return buy, sell, total, imb

    def shock_boost(self) -> float:
        """Renvoie un boost [0..SHOCK_BOOST_MAX] selon (total vs EMA)."""
        if not USE_SHOCK or self.ema_total is None or self.ema_total <= 0:
            return 0.0
        total = self.buy_sum + self.sell_sum
        ratio = total / max(1e-9, self.ema_total)
        # Écrase doucement au-delà de 3x
        norm = min(1.0, (ratio - 1.0) / 2.0) if ratio > 1.0 else 0.0
        return SHOCK_BOOST_MAX * norm


class BinanceLiqWS:
    """
    Listener WebSocket Binance Futures @aggTrade -> calcule liq_new_score en temps réel.

    - symbols: liste de symboles futures Binance (ex: ["BTCUSDT", "1000BONKUSDT"])
    - on_update(symbol, payload): callback appelé toutes les EMIT_EVERY_SEC secondes, avec:
        {
            "liq_new_score": float [0..1+boost_cappé],
            "liq_notional_5m": float,
            "liq_imbalance_5m": float,
            "liq_source": "ws",
            "ts": int (ms)
        }
    """
    def __init__(self, symbols, on_update: Callable[[str, Dict], None]):
        if websockets is None:
            raise RuntimeError("Le module 'websockets' est requis. Installe: pip install websockets")
        self.symbols = [s.upper() for s in symbols]
        self.on_update = on_update
        self.windows: Dict[str, RollingWindow] = {
            s: RollingWindow(WINDOW_SEC, SHOCK_EMA_ALPHA) for s in self.symbols
        }
        self._stop = asyncio.Event()

    def _build_url(self) -> str:
        # flux combiné: /stream?streams=symbol1@aggTrade/symbol2@aggTrade/...
        streams = "/".join(f"{s.lower()}@aggTrade" for s in self.symbols)
        return f"{WS_HOST}/stream?streams={streams}"

    async def start(self):
        backoff = RECONNECT_BASE_WAIT
        while not self._stop.is_set():
            url = self._build_url()
            try:
                async with websockets.connect(url, ping_interval=20, ping_timeout=20, max_queue=1000) as ws:
                    backoff = RECONNECT_BASE_WAIT
                    last_emit = 0.0
                    while not self._stop.is_set():
                        msg = await asyncio.wait_for(ws.recv(), timeout=30)
                        now = time.time()
                        data = json.loads(msg)

                        # format combiné: {"stream": "...", "data": {...}}
                        payload = data.get("data") or data
                        stream = data.get("stream", "")
                        # Pour sécurité, récup symbol via stream "btcusdt@aggTrade" sinon via "s" dans payload
                        symbol = payload.get("s") or (stream.split("@")[0].upper() if "@" in stream else None)
                        if not symbol or symbol not in self.windows:
                            continue

                        # aggTrade payload (fapi):
                        # p=price, q=qty, m=buyer is market maker (True => vendeur agressif), T=trade time
                        price = float(payload.get("p", 0.0))
                        qty   = float(payload.get("q", 0.0))
                        maker = bool(payload.get("m", False))  # True => buyer is maker => sell aggressor
                        ts_ms = int(payload.get("T", now * 1000))
                        ts = ts_ms / 1000.0
                        notional = price * qty

                        buy_n = 0.0
                        sell_n = 0.0
                        if maker:  # vendeur agressif (taker côté sell) -> volume "sell"
                            sell_n = notional
                        else:
                            buy_n = notional

                        self.windows[symbol].add(ts, buy_n, sell_n)

                        # émission périodique
                        if now - last_emit >= EMIT_EVERY_SEC:
                            last_emit = now
                            await self._emit_updates()

            except (asyncio.TimeoutError, asyncio.CancelledError):
                if self._stop.is_set(): break
            except Exception:
                # On attend avant de se reconnecter
                await asyncio.sleep(backoff)
                backoff = min(RECONNECT_MAX_WAIT, backoff * 1.6)

    async def _emit_updates(self):
        now_ts = time.time()
        for symbol, win in self.windows.items():
            buy, sell, total, imb = win.snapshot(now_ts)
            base = min(1.0, total / max(1.0, NOTIONAL_NORM))
            score = BASE_WEIGHT * base + IMB_WEIGHT * imb

            # Boost choc vs EMA (optionnel)
            score += win.shock_boost()
            score = min(1.0, max(0.0, score))

            out = {
                "liq_new_score": float(score),
                "liq_notional_5m": float(total),
                "liq_imbalance_5m": float(imb),
                "liq_source": "ws",
                "ts": int(now_ts * 1000),
            }
            try:
                self.on_update(symbol, out)
            except Exception:
                # on évite de casser la boucle si le callback throw
                pass

    async def stop(self):
        self._stop.set()


# ---------------------- Intégration simple ----------------------
# 1) Lance le listener une fois au démarrage (dans main/scanner):
#
#   from institutional_liq_ws import BinanceLiqWS
#
#   shared_inst_cache = {}  # {symbol: dict(...)}
#
#   def on_liq(symbol, pack):
#       # merge dans ton cache institutionnel
#       if symbol not in shared_inst_cache:
#           shared_inst_cache[symbol] = {}
#       shared_inst_cache[symbol].update(pack)
#
#   ws = BinanceLiqWS(symbols=["BTCUSDT","1000BONKUSDT","1000CHEEMSUSDT"], on_update=on_liq)
#   asyncio.create_task(ws.start())
#
# 2) Dans ton build 'inst' par symbole (institutional_data.py / scanner.py):
#
#   inst = {... OI/Funding/...}
#   inst.update(shared_inst_cache.get(symbol, {}))
#
#   # analyze_signal utilisera liq_new_score en priorité (via le code déjà fourni).
#
# 3) À l’arrêt propre:
#   await ws.stop()
