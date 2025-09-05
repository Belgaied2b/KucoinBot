# -*- coding: utf-8 -*-
"""
main.py — Boucle event-driven optimisée
"""

import os, asyncio, logging, math, time
from typing import Dict, Any, Tuple
from ws_router import EventBus, PollingSource
from execution_sfi import SFIEngine
from risk_guard import RiskGuard
from meta_policy import MetaPolicy
from perf_metrics import register_signal_perf, update_perf_for_symbol
from kucoin_utils import fetch_all_symbols, fetch_klines

try:
    import analyze_bridge as analyze_signal
except Exception:
    import analyze_signal  # fallback

TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID")

H1_LIMIT = int(os.getenv("H1_LIMIT", "500"))
H4_LIMIT = int(os.getenv("H4_LIMIT", "400"))
H1_REFRESH_SEC = int(os.getenv("H1_REFRESH_SEC", "60"))
H4_REFRESH_SEC = int(os.getenv("H4_REFRESH_SEC", "300"))
ANALYSIS_MIN_INTERVAL_SEC = int(os.getenv("ANALYSIS_MIN_INTERVAL_SEC", "15"))

_KLINE_CACHE: Dict[str, Dict[str, Any]] = {}
_LAST_ANALYSIS_TS: Dict[str, float] = {}

def fmt_price(x):
    if x is None: return "—"
    if x == 0: return "0"
    d = 2 if x >= 1 else min(8, int(abs(math.log10(1.0/x))) + 2)
    return f"{x:.{d}f}"

def send_telegram(text: str):
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        logging.info("[TG OFF] %s", text); return
    import requests
    url=f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    try:
        requests.post(url, json={"chat_id": TELEGRAM_CHAT_ID, "text": text, "parse_mode":"Markdown", "disable_web_page_preview": True}, timeout=10)
    except Exception as e:
        logging.error("Telegram KO: %s", e)

def _get_klines_cached(symbol: str) -> Tuple[Any, Any]:
    now = time.time()
    ent = _KLINE_CACHE.get(symbol, {})
    need_h1 = ("h1" not in ent) or (now - ent.get("ts_h1", 0) > H1_REFRESH_SEC)
    need_h4 = ("h4" not in ent) or (now - ent.get("ts_h4", 0) > H4_REFRESH_SEC)

    if need_h1:
        df_h1 = fetch_klines(symbol, interval="1h", limit=H1_LIMIT)
        ent["h1"] = df_h1
        ent["ts_h1"] = now
    if need_h4:
        df_h4 = fetch_klines(symbol, interval="4h", limit=H4_LIMIT)
        ent["h4"] = df_h4
        ent["ts_h4"] = now

    _KLINE_CACHE[symbol] = ent
    return ent.get("h1"), ent.get("h4")

async def handle_symbol_event(ev: Dict[str, Any], rg: RiskGuard, policy: MetaPolicy):
    symbol = ev.get("symbol")
    etype = ev.get("type")
    if etype not in ("bar",):
        return

    last = _LAST_ANALYSIS_TS.get(symbol, 0.0)
    if time.time() - last < ANALYSIS_MIN_INTERVAL_SEC:
        return
    _LAST_ANALYSIS_TS[symbol] = time.time()

    try:
        df_h1, df_h4 = _get_klines_cached(symbol)
    except Exception as e:
        logging.warning("[%s] fetch_klines KO: %s", symbol, e)
        return
    if df_h1 is None and df_h4 is None:
        logging.warning("[%s] klines vides", symbol)
        return

    try:
        res = analyze_signal.analyze_signal(symbol=symbol, df_h1=df_h1, df_h4=df_h4)
    except Exception as e:
        logging.warning("[%s] analyze_signal KO: %s", symbol, e)
        return

    if not isinstance(res, dict) or not res.get("valid", False):
        # ⬇️ utilise le DF déjà en cache (évite un fetch H1)
        try: update_perf_for_symbol(symbol, df_h1=df_h1)
        except Exception: pass
        return

    ok, reason = rg.can_enter(symbol, ws_latency_ms=50, last_data_age_s=5)
    if not ok:
        logging.info("[%s] Blocké par risk_guard: %s", symbol, reason)
        return

    arm, weight, label = policy.choose({"atr_pct": res.get("atr_pct", 0), "adx_proxy": res.get("adx_proxy", 0)})
    if weight < 0.25 and res.get("rr", 0) < 1.5:
        try: update_perf_for_symbol(symbol, df_h1=df_h1)  # ⬅️ idem, pas de refetch
        except Exception: pass
        return

    side = res.get("side", "long").lower()
    entry = res.get("entry")
    sl, tp1, tp2 = res.get("sl"), res.get("tp1"), res.get("tp2")
    value_usdt = float(os.environ.get("ORDER_VALUE_USDT", "20"))

    eng = SFIEngine(symbol, side, value_usdt, sl, tp1, tp2)
    orders = eng.place_initial(entry_hint=entry)
    eng.maybe_requote()

    msg = (f"🧠 *{symbol}* — *{side.upper()}* via *{label}*\n"
           f"RR: *{res.get('rr','—')}*  |  Entrée: *{fmt_price(entry)}*  |  SL: *{fmt_price(sl)}*  |  TP1: *{fmt_price(tp1)}*  TP2: *{fmt_price(tp2)}*\n"
           f"Ordres: {orders}")
    send_telegram(msg)

    key = f"{symbol}:{side}:{fmt_price(entry)}:{round(res.get('rr',0),2)}"
    register_signal_perf(key, symbol, side, entry)
    try: update_perf_for_symbol(symbol, df_h1=df_h1)  # ⬅️ encore sans refetch
    except Exception: pass

async def main():
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
    # ↓↓ coupe le bruit httpx (les lignes "HTTP Request: GET ...")
    logging.getLogger("httpx").setLevel(logging.WARNING)

    try:
        symbols = [s for s in fetch_all_symbols() if s.endswith("USDTM")]
    except Exception:
        symbols = []
    if not symbols:
        logging.warning("Aucun symbole.")
        return

    bus = EventBus()
    src = PollingSource(symbols, interval_sec=int(os.getenv("WS_POLL_SEC", "5")))
    bus.add_source(src.__aiter__())
    await bus.start()

    rg = RiskGuard()
    policy = MetaPolicy()

    async for ev in bus.events():
        try:
            await handle_symbol_event(ev, rg, policy)
        except Exception as e:
            logging.error("handle_symbol_event: %s", e)

if __name__ == "__main__":
    asyncio.run(main())
