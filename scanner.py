# scanner.py — scan séquentiel qui défile, 400 perp USDT, ordre alpha (XBT→Z), logs filtrés
import asyncio, time, uuid, os
from collections import deque, Counter
import logging
import pandas as pd
import httpx
from typing import Optional, List, Dict

from config import SETTINGS
from institutional_aggregator import InstitutionalAggregator
from analyze_signal import analyze_signal, Decision
from kucoin_trader import KucoinTrader
from kucoin_ws import KucoinPrivateWS
from order_manager import OrderManager
from orderflow_features import compute_atr
from kucoin_utils import (
    fetch_klines, fetch_symbol_meta, round_price,
    common_usdt_symbols
)
from telegram_notifier import send_msg
from institutional_data import get_macro_total_mcap, get_macro_total2, get_macro_btc_dominance
from adverse_selection import should_cancel_or_requote
from logger_utils import get_logger
from institutional_data import map_symbol_to_binance, get_liq_pack, get_funding_rate

rootlog = get_logger("scanner")

# ====== Anti-duplicate signals (cooldown) ======
_LAST_SIGNAL_KEY: Dict[str,str] = {}
_LAST_SIGNAL_TS: Dict[str,float] = {}
def _is_duplicate_signal(symbol: str, key: str, cooldown_sec: int) -> bool:
    now = time.time()
    last_key = _LAST_SIGNAL_KEY.get(symbol)
    last_ts  = _LAST_SIGNAL_TS.get(symbol, 0.0)
    if last_key == key and (now - last_ts) < cooldown_sec:
        return True
    _LAST_SIGNAL_KEY[symbol] = key
    _LAST_SIGNAL_TS[symbol]  = now
    return False

# ====== Config locaux & constantes ======
LIQ_REFRESH_SEC      = float(getattr(SETTINGS, "liq_refresh_sec", 30))
REQ_SCORE_MIN        = float(getattr(SETTINGS, "req_score_min", 1.2))
INST_COMPONENTS_MIN  = int(getattr(SETTINGS, "inst_components_min", 2))
OI_MIN               = float(getattr(SETTINGS, "oi_req_min", 0.25))
DELTA_MIN            = float(getattr(SETTINGS, "delta_req_min", 0.30))
FUND_MIN             = float(getattr(SETTINGS, "funding_req_min", 0.05))
LIQ_MIN              = float(getattr(SETTINGS, "liq_req_min", 0.20))
BOOK_MIN             = float(getattr(SETTINGS, "book_req_min", 0.30))
USE_BOOK             = bool(getattr(SETTINGS, "use_book_imbal", False))

PERSIST_WIN          = int(getattr(SETTINGS, "persist_win", 2))
PERSIST_MIN_OK       = int(getattr(SETTINGS, "persist_min_ok", 1))
SYMBOL_COOLDOWN_SEC  = int(getattr(SETTINGS, "symbol_cooldown_sec", 45))
MIN_LIQ_NORM         = float(getattr(SETTINGS, "min_liq_norm", 0.0))

BINANCE_BASE         = "https://fapi.binance.com"
FUND_REF             = float(getattr(SETTINGS, "funding_ref", 0.00008))
OI_DELTA_REF         = float(getattr(SETTINGS, "oi_delta_ref", 0.004))
HTTP_TIMEOUT         = float(getattr(SETTINGS, "http_timeout_sec", 6.0))
DEFAULT_LEVERAGE     = int(getattr(SETTINGS, "default_leverage", 10))

# ====== Mode scan séquentiel (défilement) ======
SCAN_WORKERS         = int(getattr(SETTINGS, "scan_workers", 1))  # 1 = défilement pur
SCAN_TIME_PER_SYMBOL = float(getattr(SETTINGS, "scan_time_per_symbol_sec", 1.0))

# ====== Cadence fine (patch) ======
INNER_SLEEP_SEC      = float(getattr(SETTINGS, "inner_sleep_sec", 0.15))  # 3–6 ticks/passe
WARMUP_SECONDS       = float(getattr(SETTINGS, "warmup_seconds", 0.30))   # warmup bref
WARMUP_FRAC          = float(getattr(SETTINGS, "warmup_frac", 0.30))      # % de la fenêtre, bornage dynamique

BINANCE_FUTURES_API  = "https://fapi.binance.com"

# ====== Debug institutionnel (logs de gate) ======
INST_DEBUG = bool(int(os.getenv("INST_DEBUG", "1"))) if not hasattr(SETTINGS, "inst_debug") else bool(getattr(SETTINGS, "inst_debug"))
GATE_STATS_PERIOD_SEC = int(getattr(SETTINGS, "gate_stats_period_sec", int(os.getenv("GATE_STATS_PERIOD_SEC", "60"))))
_GATE_STATS = {"cnt": Counter(), "last_log": 0.0}

def _fmt(v, d=2):
    try:
        return f"{float(v):.{d}f}"
    except Exception:
        return str(v)

def _gate_fail_reasons(inst: dict, dyn_req: float, score: float, comps_ok: int, persist_sum: int,
                       persist_need: int, cooldown_left: float, warmup_left: float,
                       min_liq_norm: float, liq_norm: float, use_book: bool) -> list[str]:
    reasons = []
    if score < dyn_req:
        reasons.append(f"below_score:{_fmt(score)}/{_fmt(dyn_req)}")
    if float(inst.get("oi_score", 0.0))        < OI_MIN:    reasons.append(f"oi<{_fmt(OI_MIN)}")
    if float(inst.get("delta_score", 0.0))     < DELTA_MIN: reasons.append(f"delta<{_fmt(DELTA_MIN)}")
    if float(inst.get("funding_score", 0.0))   < FUND_MIN:  reasons.append(f"fund<{_fmt(FUND_MIN)}")
    if float(inst.get("liq_new_score", inst.get("liq_score", 0.0))) < LIQ_MIN:
        reasons.append(f"liq<{_fmt(LIQ_MIN)}")
    if use_book and float(inst.get("book_imbal_score", 0.0)) < BOOK_MIN:
        reasons.append(f"book<{_fmt(BOOK_MIN)}")
    if persist_sum < persist_need:
        reasons.append(f"persist:{persist_sum}/{persist_need}")
    if MIN_LIQ_NORM > 0 and liq_norm and liq_norm < min_liq_norm:
        reasons.append(f"liq_norm<{int(min_liq_norm)}({int(liq_norm)})")
    if cooldown_left > 0:
        reasons.append(f"cooldown:{int(cooldown_left)}s")
    if warmup_left > 0:
        reasons.append(f"warmup:{_fmt(warmup_left,1)}s")
    return reasons or ["unknown"]

def _log_gate(symbol: str, price: float, inst: dict, score: float, dyn_req: float, comps_ok: int,
              persist_sum: int, persist_need: int, persist_next: int,
              cooldown_left: float, warmup_left: float,
              liq_norm: float, gate_now: bool, use_book: bool, extra_reasons: list[str] | None = None):
    oi   = float(inst.get("oi_score", 0.0) or 0.0)
    dlt  = float(inst.get("delta_score", 0.0) or 0.0)
    fund = float(inst.get("funding_score", 0.0) or 0.0)
    liq  = float(inst.get("liq_new_score", inst.get("liq_score", 0.0)) or 0.0)
    book = float(inst.get("book_imbal_score", 0.0) or 0.0)
    cvd  = float(inst.get("delta_cvd_usd", 0.0) or 0.0)
    base_msg = (
        f"[GATE] pass={gate_now} price={_fmt(price,4)} "
        f"s={_fmt(score)}/{_fmt(dyn_req)} comps={comps_ok}/{INST_COMPONENTS_MIN} "
        f"OI={_fmt(oi)}/{_fmt(OI_MIN)} Δ={_fmt(dlt)}/{_fmt(DELTA_MIN)} F={_fmt(fund)}/{_fmt(FUND_MIN)} "
        f"Liq={_fmt(liq)}/{_fmt(LIQ_MIN)}"
    )
    if use_book:
        base_msg += f" Book={_fmt(book)}/{_fmt(BOOK_MIN)}"
    base_msg += f" liq_norm={int(liq_norm)} cvd={int(cvd)} persist={persist_sum}/{persist_need}(next={persist_next})"
    if cooldown_left > 0 or warmup_left > 0:
        base_msg += f" cooldn={int(cooldown_left)}s warmup={_fmt(warmup_left,1)}s"
    reasons = extra_reasons or []
    logger = get_logger("scanner.symbol", symbol)
    logger.info(base_msg + (f" reasons={','.join(reasons)}" if reasons else ""))

def _bump_gate_stats(reasons: list[str]):
    if reasons:
        _GATE_STATS["cnt"].update(reasons)

def _maybe_log_gate_stats():
    now = time.time()
    if now - _GATE_STATS["last_log"] < GATE_STATS_PERIOD_SEC:
        return
    _GATE_STATS["last_log"] = now
    if not _GATE_STATS["cnt"]:
        return
    top = ", ".join([f"{k}:{v}" for k, v in _GATE_STATS["cnt"].most_common(6)])
    rootlog.info(f"[GATE-STATS] top blockers (≈{GATE_STATS_PERIOD_SEC}s) → {top}")
    _GATE_STATS["cnt"].clear()

# ====== CVD Binance (delta tick-by-tick) ======
class BinanceCVD:
    def __init__(self, window_sec: int = 300, http_timeout: float = 5.0, ref_notional: float = 150_000.0):
        from collections import deque
        self.window_ms = int(window_sec * 1000)
        self.timeout   = http_timeout
        self.ref       = float(ref_notional)
        self.state = {}  # bsym -> {last_id:int|None, deq:deque[(ts:int, signed_notional:float)]}

    def _trim(self, deq):
        cutoff = int(time.time() * 1000) - self.window_ms
        while deq and deq[0][0] < cutoff:
            deq.popleft()

    def _fetch_aggtrades(self, bsym: str, from_id: Optional[int]):
        params = {"symbol": bsym, "limit": 1000}
        if from_id is not None:
            params["fromId"] = int(from_id)
        try:
            r = httpx.get(f"{BINANCE_FUTURES_API}/fapi/v1/aggTrades", params=params, timeout=self.timeout)
        except Exception:
            return []
        if r.status_code != 200:
            return []
        try:
            return r.json() or []
        except Exception:
            return []

    def update(self, bsym: str):
        from collections import deque
        st = self.state.get(bsym)
        if st is None:
            st = {"last_id": None, "deq": deque()}
            self.state[bsym] = st

        trades = self._fetch_aggtrades(bsym, st["last_id"] + 1 if st["last_id"] is not None else None)
        for t in trades:
            try:
                tid = int(t.get("a")); ts = int(t.get("T"))
                p = float(t.get("p", 0.0)); q = float(t.get("q", 0.0))
                is_buyer_maker = bool(t.get("m", False))
            except Exception:
                continue
            notion = p * q
            signed = -notion if is_buyer_maker else +notion
            st["deq"].append((ts, signed))
            st["last_id"] = tid

        self._trim(st["deq"])
        total = 0.0; buy_n = 0.0; sell_n = 0.0
        for _, val in st["deq"]]:
            total += val
            if val >= 0: buy_n += val
            else: sell_n += (-val)

        score = 0.0
        if self.ref > 0:
            score = max(0.0, min(1.0, abs(total) / self.ref))

        return {"cvd_notional": total, "buy_notional": buy_n, "sell_notional": sell_n, "delta_score": score}

# ====== Helpers OI / Funding ======
def _norm01(x: float, ref: float) -> float:
    if ref <= 0:
        return 0.0
    try:
        return max(0.0, min(1.0, float(x) / float(ref)))
    except Exception:
        return 0.0

def _fetch_oi_score_binance(symbol: str) -> Optional[float]:
    bsym = map_symbol_to_binance(symbol)
    if not bsym:
        return None
    try:
        r = httpx.get(
            f"{BINANCE_BASE}/futures/data/openInterestHist",
            params={"symbol": bsym, "period": "5m", "limit": 2},
            timeout=HTTP_TIMEOUT, headers={"Accept": "application/json"}
        )
        if r.status_code != 200:
            return None
        arr = r.json() or []
        if len(arr) < 2:
            return None
        a, b2 = arr[-2], arr[-1]
        oi1 = float(a.get("sumOpenInterest", a.get("openInterest", 0.0)) or 0.0)
        oi2 = float(b2.get("sumOpenInterest", b2.get("openInterest", 0.0)) or 0.0)
        if oi1 <= 0:
            return None
        delta_pct = abs((oi2 - oi1) / oi1)
        return _norm01(delta_pct, OI_DELTA_REF)
    except Exception:
        return None

def _fetch_funding_score_binance(symbol: str) -> Optional[float]:
    try:
        r = get_funding_rate(symbol)
        if r is None:
            return None
        return _norm01(abs(float(r)), FUND_REF)
    except Exception:
        return None

# ====== Macro cache ======
class MacroCache:
    def __init__(self):
        self.last = 0.0
        self.data = {}
    def refresh(self):
        now = time.time()
        if now - self.last < float(getattr(SETTINGS, "macro_refresh_minutes", 5)) * 60.0:
            return self.data
        total  = get_macro_total_mcap()
        total2 = get_macro_total2() if getattr(SETTINGS, "use_total2", False) else 0.0
        dom    = get_macro_btc_dominance()
        self.data = {"TOTAL": total, "TOTAL2": total2, "BTC_DOM": dom, "TOTAL_PCT": 0.0, "TOTAL2_PCT": 0.0}
        self.last = now
        return self.data

# ====== OHLC local 1m ======
class OHLCV1m:
    def __init__(self, meta: dict):
        self.df: Dict[str, pd.DataFrame] = {}
        self.meta = meta  # display_symbol -> {symbol_api, tickSize, pricePrecision, ...}

    def bootstrap(self, sym: str):
        logger = get_logger("scanner.bootstrap", sym)
        try:
            sym_api = self.meta[sym]["symbol_api"]
            df = fetch_klines(sym_api, granularity=1, limit=300)
            self.df[sym] = df
        except Exception as e:
            logger.warning(f"Bootstrap klines fallback: {e}", extra={"symbol": sym})
            now = int(time.time() * 1000)
            base = 50000.0
            rows = [{"time": now - (299 - i) * 60_000, "open": base, "high": base, "low": base, "close": base, "volume": 0.0} for i in range(300)]
            self.df[sym] = pd.DataFrame(rows)

    def on_price(self, sym, price, ts, vol=0.0):
        if sym not in self.df:
            self.bootstrap(sym)
        df = self.df[sym]
        minute = (ts // 60000) * 60000
        if int(df.iloc[-1]["time"]) == minute:
            idx = df.index[-1]
            p = float(price)
            df.at[idx, "close"]  = p
            df.at[idx, "high"]   = max(float(df.at[idx, "high"]), p)
            df.at[idx, "low"]    = min(float(df.at[idx, "low"]),  p)
            df.at[idx, "volume"] = float(df.at[idx, "volume"]) + float(vol)
        else:
            last_close = float(df.iloc[-1]["close"])
            p = float(price)
            new_row = {"time": minute, "open": last_close, "high": p, "low": p, "close": p, "volume": float(vol)}
            self.df[sym] = pd.concat([df, pd.DataFrame([new_row])], ignore_index=True).tail(2000)

    def frame(self, sym):
        if sym not in self.df:
            self.bootstrap(sym)
        return self.df[sym]

# ====== Scoring ======
def _tick_shift(symbol: str, px: float, ticks: int, meta, default_tick: float) -> float:
    tick = float(meta.get(symbol, {}).get("tickSize", default_tick))
    return px + ticks * tick

def _compute_global_score_sum(inst: dict) -> float:
    w_oi   = float(getattr(SETTINGS, "w_oi", 0.6))
    w_fund = float(getattr(SETTINGS, "w_funding", 0.2))
    w_delta= float(getattr(SETTINGS, "w_delta", 0.2))
    w_liq  = float(getattr(SETTINGS, "w_liq", 0.5))
    w_book = float(getattr(SETTINGS, "w_book_imbal", 0.0))
    use_book = bool(getattr(SETTINGS, "use_book_imbal", USE_BOOK))

    total = 0.0
    def add(key: str, w: float):
        nonlocal total
        if w <= 0: return
        v = inst.get(key)
        if v is None: return
        try:
            total += w * float(v)
        except Exception:
            pass

    add("oi_score", w_oi)
    add("delta_score", w_delta)
    add("funding_score", w_fund)
    add("liq_new_score" if "liq_new_score" in inst else "liq_score", w_liq)
    if use_book:
        add("book_imbal_score", w_book)
    return float(total)

def _components_ok(inst: dict) -> int:
    oi_ok   = float(inst.get("oi_score", 0.0))        >= OI_MIN
    dlt_ok  = float(inst.get("delta_score", 0.0))     >= DELTA_MIN
    fund_ok = float(inst.get("funding_score", 0.0))   >= FUND_MIN
    liq_ok  = float(inst.get("liq_new_score", inst.get("liq_score", 0.0))) >= LIQ_MIN
    if USE_BOOK:
        book_ok = float(inst.get("book_imbal_score", 0.0)) >= BOOK_MIN
        return int(oi_ok)+int(dlt_ok)+int(fund_ok)+int(liq_ok)+int(book_ok)
    return int(oi_ok)+int(dlt_ok)+int(fund_ok)+int(liq_ok)

def _active_weight_sum(inst: dict, w_oi: float, w_fund: float, w_dlt: float, w_liq: float, w_book: float, use_book: bool) -> float:
    s = 0.0
    if inst.get("oi_score")      is not None: s += w_oi
    if inst.get("funding_score") is not None: s += w_fund
    if inst.get("delta_score")   is not None: s += w_dlt
    if (inst.get("liq_new_score") is not None) or (inst.get("liq_score") is not None): s += w_liq
    if use_book and (inst.get("book_imbal_score") is not None): s += w_book
    return float(s)

def _dyn_req_score(inst: dict, w_cfg: tuple, use_book: bool) -> float:
    w_oi, w_fund, w_dlt, w_liq, w_book = w_cfg
    active = _active_weight_sum(inst, w_oi, w_fund, w_dlt, w_liq, w_book, use_book)
    base_req = float(getattr(SETTINGS, "req_score_min", 1.2))
    return max(0.0, min(base_req, active * 0.80))

# ====== Orders utils ======
def _ensure_leverage_if_needed(trader: KucoinTrader, sym_api: str, logger, want_lev: int = DEFAULT_LEVERAGE):
    set_lev = getattr(trader, "set_leverage", None)
    if callable(set_lev):
        try:
            ok, resp = set_lev(sym_api, want_lev)
            logger.info(f"LEV ensure {sym_api} -> ok={ok} resp={resp}")
        except Exception as e:
            logger.warning(f"LEV ensure failed: {e}")

def _place_limit_with_lev_retry(trader: KucoinTrader, sym_api: str, side: str, px: float, client_oid: str, post_only: bool, logger, value_qty: Optional[float] = None, leverage: Optional[int] = None):
    ok, res = trader.place_limit(sym_api, side, px, client_oid, post_only=post_only, value_qty=value_qty)
    if ok:
        return ok, res
    try:
        code = (res or {}).get("code") if isinstance(res, dict) else None
        msg  = (res or {}).get("msg")  if isinstance(res, dict) else str(res)
    except Exception:
        code, msg = None, str(res)
    if (code == "100001") or ("Leverage parameter invalid" in (msg or "")):
        _ensure_leverage_if_needed(trader, sym_api, logger, want_lev=(leverage or DEFAULT_LEVERAGE))
        ok2, res2 = trader.place_limit(sym_api, side, px, client_oid, post_only=post_only, value_qty=value_qty)
        return ok2, res2
    return ok, res

def _place_ioc_with_lev_retry(trader: KucoinTrader, sym_api: str, side: str, px: float, logger, value_qty: Optional[float] = None, leverage: Optional[int] = None):
    ok, res = trader.place_limit_ioc(sym_api, side, px, value_qty=value_qty)
    if ok:
        return ok, res
    try:
        code = (res or {}).get("code") if isinstance(res, dict) else None
        msg  = (res or {}).get("msg")  if isinstance(res, dict) else str(res)
    except Exception:
        code, msg = None, str(res)
    if (code == "100001") or ("Leverage parameter invalid" in (msg or "")):
        _ensure_leverage_if_needed(trader, sym_api, logger, want_lev=(leverage or DEFAULT_LEVERAGE))
        return trader.place_limit_ioc(sym_api, side, px, value_qty=value_qty)
    return ok, res

# ====== Symbol loop (une passe courte par symbole, puis on défile) ======
async def run_symbol(symbol: str, kws: KucoinPrivateWS, macro: 'MacroCache', meta: dict, time_budget_sec: Optional[float] = None):
    logger = get_logger("scanner.symbol", symbol)
    w_cfg  = (
        float(getattr(SETTINGS, "w_oi", 0.6)),
        float(getattr(SETTINGS, "w_funding", 0.2)),
        float(getattr(SETTINGS, "w_delta", 0.2)),
        float(getattr(SETTINGS, "w_liq", 0.5)),
        float(getattr(SETTINGS, "w_book_imbal", 0.0)),
    )
    agg    = InstitutionalAggregator(symbol, w_cfg)
    trader = KucoinTrader()
    ohlc   = OHLCV1m(meta)
    om     = OrderManager()

    sym_api = meta.get(symbol, {}).get("symbol_api", symbol)
    cvd     = BinanceCVD(
        window_sec=int(getattr(SETTINGS, "delta_window_sec", 300)),
        http_timeout=float(getattr(SETTINGS, "http_timeout_sec", 6.0)),
        ref_notional=float(getattr(SETTINGS, "delta_notional_ref", 150_000.0)),
    )

    last_hb = 0.0
    last_liq_fetch = 0.0
    liq_pack_cache = {}
    last_trade_ts = 0.0
    last_oi_fund_fetch = 0.0
    oi_fund_cache = {}
    persist_buf = deque(maxlen=PERSIST_WIN)

    def on_order(msg):
        oid = msg.get("clientOid") or ""
        sym = msg.get("symbol", "")
        status = msg.get("status", "")
        avgp = None
        try: avgp = float(msg.get("avgFillPrice", msg.get("matchPrice", 0.0)) or 0.0)
        except Exception: pass
        filled_value = None
        try: filled_value = float(msg.get("filledValue", 0.0))
        except Exception: pass
        if oid:
            logger.debug(f"WS order event status={status} avgFill={avgp} filledValue={filled_value}", extra={"symbol": symbol})
            om.set_pending_status(oid, status, avg_fill_price=avgp, filled_value=filled_value)
            if status in ("filled", "partialFilled", "match") and sym:
                if avgp and sym in om.pos:
                    om.update_entry_with_fill(sym, avgp)
            if status in ("filled", "cancel"):
                om.remove_pending(oid)
    kws.on("order", on_order)

    async def feed_ohlc():
        while True:
            try:
                if agg.state.price is not None:
                    ohlc.on_price(symbol, agg.state.price, int(time.time() * 1000), vol=abs(agg.state.delta))
                await asyncio.sleep(0.2)
            except Exception:
                logger.exception("feed_ohlc loop error", extra={"symbol": symbol})
                await asyncio.sleep(0.5)

    # Démarrage des tâches + petit délai (fenêtre démarre après)
    task_agg  = asyncio.create_task(agg.run())
    task_feed = asyncio.create_task(feed_ohlc())
    logger.info("symbol task started", extra={"symbol": symbol})
    await asyncio.sleep(1.5)

    loop_started = time.time()  # la fenêtre démarre ici
    started_at   = loop_started

    # Warmup effectif borné par la fenêtre (si définie)
    warmup_eff = WARMUP_SECONDS
    if time_budget_sec is not None and time_budget_sec > 0:
        warmup_eff = min(WARMUP_SECONDS, max(0.0, time_budget_sec * WARMUP_FRAC))

    try:
        while True:
            if time_budget_sec is not None and (time.time() - loop_started) > time_budget_sec:
                logger.info("scan window done", extra={"symbol": symbol})
                break

            try:
                # ---- cadence plus fine
                await asyncio.sleep(INNER_SLEEP_SEC)

                _, inst = agg.get_meta_score()
                df = ohlc.frame(symbol)
                price = float(df["close"].iloc[-1])
                macro_data = macro.refresh()

                # LIQ PACK (throttle)
                if (time.time() - last_liq_fetch) > LIQ_REFRESH_SEC:
                    last_liq_fetch = time.time()
                    try:
                        liq_pack_cache = get_liq_pack(symbol)
                        if liq_pack_cache.get("liq_source") != "none":
                            logger.info(
                                f"[LIQ] src={liq_pack_cache.get('liq_source')} "
                                f"sc={liq_pack_cache.get('liq_new_score', 0.0):.3f} "
                                f"N5m={liq_pack_cache.get('liq_notional_5m', 0.0):.0f} "
                                f"imb={liq_pack_cache.get('liq_imbalance_5m', 0.0):.3f} "
                                f"norm={liq_pack_cache.get('liq_norm', 0.0):.0f}",
                                extra={"symbol": symbol}
                            )
                    except Exception as e:
                        logger.exception(f"[LIQ PACK] fetch error: {e}", extra={"symbol": symbol})

                # OI/Funding (throttle)
                if (time.time() - last_oi_fund_fetch) > float(getattr(SETTINGS, "oi_fund_refresh_sec", 30.0)):
                    last_oi_fund_fetch = time.time()
                    try:
                        oi_sc = _fetch_oi_score_binance(symbol)
                        if oi_sc is not None:
                            oi_fund_cache["oi_score"] = float(oi_sc)
                        fund_sc = _fetch_funding_score_binance(symbol)
                        if fund_sc is not None:
                            oi_fund_cache["funding_score"] = float(fund_sc)
                        if oi_fund_cache:
                            logger.info(f"[OI/FUND] oi={oi_fund_cache.get('oi_score')} fund={oi_fund_cache.get('funding_score')}", extra={"symbol": symbol})
                    except Exception as e:
                        logger.exception(f"[OI/FUND ENRICH] error: {e}", extra={"symbol": symbol})

                inst_merged = {**inst, **(liq_pack_cache or {}), **(oi_fund_cache or {})}

                # Δ tick-by-tick via Binance
                try:
                    bsym = map_symbol_to_binance(symbol)
                    if bsym:
                        cvd_stats = cvd.update(bsym)
                        if cvd_stats:
                            inst_merged["delta_score"]   = float(cvd_stats["delta_score"])
                            inst_merged["delta_cvd_usd"] = float(cvd_stats["cvd_notional"])
                            inst_merged["delta_buy_usd"] = float(cvd_stats["buy_notional"])
                            inst_merged["delta_sell_usd"]= float(cvd_stats["sell_notional"])
                except Exception:
                    logger.exception("CVD update failed", extra={"symbol": symbol})

                # Liquidity floor optionnel
                if MIN_LIQ_NORM > 0:
                    liq_norm = float(inst_merged.get("liq_norm", 0.0) or 0.0)
                    if liq_norm and liq_norm < MIN_LIQ_NORM:
                        if time.time() - last_hb > 30:
                            last_hb = time.time()
                            logger.info(f"hb illiq p={price:.4f} norm={liq_norm:.0f}", extra={"symbol": symbol})
                        # log de gate pour explication
                        if INST_DEBUG:
                            use_book = bool(getattr(SETTINGS, "use_book_imbal", False))
                            persist_sum = sum(persist_buf)
                            warmup_left = max(0.0, warmup_eff - (time.time() - started_at))
                            cooldown_left = max(0.0, float(SYMBOL_COOLDOWN_SEC) - (time.time() - last_trade_ts)) if last_trade_ts > 0 else 0.0
                            score_tmp = _compute_global_score_sum(inst_merged)
                            dyn_req_tmp = _dyn_req_score(inst_merged, (
                                float(getattr(SETTINGS, "w_oi", 0.6)),
                                float(getattr(SETTINGS, "w_funding", 0.2)),
                                float(getattr(SETTINGS, "w_delta", 0.2)),
                                float(getattr(SETTINGS, "w_liq", 0.5)),
                                float(getattr(SETTINGS, "w_book_imbal", 0.0)),
                            ), use_book)
                            comps_ok_tmp = _components_ok(inst_merged)
                            gate_now_tmp = (score_tmp >= dyn_req_tmp) and (comps_ok_tmp >= INST_COMPONENTS_MIN)
                            persist_next_tmp = persist_sum + (1 if gate_now_tmp else 0)
                            reasons = _gate_fail_reasons(inst_merged, dyn_req_tmp, score_tmp, comps_ok_tmp, persist_sum, PERSIST_MIN_OK,
                                                         cooldown_left, warmup_left, MIN_LIQ_NORM, liq_norm, use_book)
                            _log_gate(symbol, price, inst_merged, score_tmp, dyn_req_tmp, comps_ok_tmp,
                                      persist_sum, PERSIST_MIN_OK, persist_next_tmp,
                                      cooldown_left, warmup_left, liq_norm, False, use_book, reasons)
                            _bump_gate_stats(reasons); _maybe_log_gate_stats()
                        continue

                # Score
                score = _compute_global_score_sum(inst_merged)
                inst_merged["score"] = score

                use_book = bool(getattr(SETTINGS, "use_book_imbal", False))
                dyn_req  = _dyn_req_score(inst_merged, (
                    float(getattr(SETTINGS, "w_oi", 0.6)),
                    float(getattr(SETTINGS, "w_funding", 0.2)),
                    float(getattr(SETTINGS, "w_delta", 0.2)),
                    float(getattr(SETTINGS, "w_liq", 0.5)),
                    float(getattr(SETTINGS, "w_book_imbal", 0.0)),
                ), use_book)

                comps_ok = _components_ok(inst_merged)
                if comps_ok >= INST_COMPONENTS_MIN and score < dyn_req:
                    score = dyn_req

                liq_val = float(inst_merged.get("liq_new_score", inst_merged.get("liq_score", 0.0)) or 0.0)
                boost = 0.0
                if liq_val >= max(0.75, LIQ_MIN): boost = max(boost, 0.25)
                if float(inst_merged.get("delta_score", 0.0)) >= max(0.75, DELTA_MIN): boost = max(boost, 0.20)
                if float(inst_merged.get("oi_score", 0.0))    >= max(0.75, OI_MIN):    boost = max(boost, 0.15)
                if float(inst_merged.get("funding_score", 0.0))>= max(0.75, FUND_MIN): boost = max(boost, 0.10)
                score += boost
                inst_merged["score"] = score

                # Heartbeat allégé
                if time.time() - last_hb > 30:
                    last_hb = time.time()
                    logger.info(
                        f"hb p={price:.4f} s={score:.2f} oi={inst_merged.get('oi_score',0):.2f} "
                        f"dlt={inst_merged.get('delta_score',0):.2f} fund={inst_merged.get('funding_score',0):.2f} "
                        f"liq={liq_val:.2f} cvd={inst_merged.get('delta_cvd_usd',0):.0f}",
                        extra={"symbol": symbol}
                    )

                # DEBUG INSTITUTIONNEL : état de passage/échec + raisons (préview avant warmup/persist)
                if INST_DEBUG:
                    persist_sum = sum(persist_buf)
                    gate_now_preview = (score >= dyn_req) and (comps_ok >= INST_COMPONENTS_MIN)
                    persist_next = persist_sum + (1 if gate_now_preview else 0)
                    warmup_left = max(0.0, warmup_eff - (time.time() - started_at))
                    cooldown_left = max(0.0, float(SYMBOL_COOLDOWN_SEC) - (time.time() - last_trade_ts)) if last_trade_ts > 0 else 0.0
                    liq_norm = float(inst_merged.get("liq_norm", 0.0) or 0.0)
                    reasons = []
                    if not gate_now_preview:
                        reasons = _gate_fail_reasons(inst_merged, dyn_req, score, comps_ok, persist_sum, PERSIST_MIN_OK,
                                                     cooldown_left, warmup_left, MIN_LIQ_NORM, liq_norm, use_book)
                    _log_gate(symbol, price, inst_merged, score, dyn_req, comps_ok,
                              persist_sum, PERSIST_MIN_OK, persist_next,
                              cooldown_left, warmup_left, liq_norm, gate_now_preview, use_book, reasons)
                    if not gate_now_preview:
                        _bump_gate_stats(reasons); _maybe_log_gate_stats()

                # Warmup (bref, dynamique)
                if (time.time() - started_at) < warmup_eff:
                    continue

                # Gate + persistance (valide sur le tick courant)
                gate_now = (score >= dyn_req) and (comps_ok >= INST_COMPONENTS_MIN)
                persist_next = sum(persist_buf) + (1 if gate_now else 0)
                if persist_next < PERSIST_MIN_OK:
                    persist_buf.append(1 if gate_now else 0)
                    continue
                persist_buf.append(1 if gate_now else 0)  # validé sur CE tick

                # Cooldown symbole
                if (time.time() - last_trade_ts) < SYMBOL_COOLDOWN_SEC:
                    continue

                # Gestion position existante
                pos = om.pos.get(symbol)
                if pos:
                    try: atr = float(compute_atr(df).iloc[-1])
                    except Exception: atr = 0.0

                    if not pos.tp1_done:
                        if (pos.side == "LONG" and price >= pos.tp1) or (pos.side == "SHORT" and price <= pos.tp1):
                            ro_side = "sell" if pos.side == "LONG" else "buy"
                            part_val = pos.qty_value * float(getattr(SETTINGS, "tp1_part", 0.5))
                            ok, _ = trader.close_reduce_market(sym_api, ro_side, value_qty=part_val)
                            if ok:
                                om.close_half_at_tp1(symbol)
                                send_msg(f"✅ {symbol} TP1 atteint — passage BE")
                                logger.info("TP1 hit → BE", extra={"symbol": symbol})
                    else:
                        trail = float(getattr(SETTINGS, "trail_mult_atr", 0.5)) * float(atr)
                        if pos.side == "LONG":
                            pos.sl = max(pos.sl, price - trail)
                            if price <= pos.sl:
                                ro_side = "sell"
                                rem_val = pos.qty_value * (1.0 - float(getattr(SETTINGS, "tp1_part", 0.5)))
                                ok, _ = trader.close_reduce_market(sym_api, ro_side, value_qty=rem_val)
                                if ok:
                                    om.close_all(symbol, "TRAIL_LONG"); send_msg(f"🛑 {symbol} Trailing stop LONG")
                        else:
                            pos.sl = min(pos.sl, price + trail)
                            if price >= pos.sl:
                                ro_side = "buy"
                                rem_val = pos.qty_value * (1.0 - float(getattr(SETTINGS, "tp1_part", 0.5)))
                                ok, _ = trader.close_reduce_market(sym_api, ro_side, value_qty=rem_val)
                                if ok:
                                    om.close_all(symbol, "TRAIL_SHORT"); send_msg(f"🛑 {symbol} Trailing stop SHORT")

                    if symbol in om.pos:
                        pos = om.pos[symbol]
                        if (pos.side == "LONG" and price >= pos.tp2) or (pos.side == "SHORT" and price <= pos.tp2):
                            ro_side = "sell" if pos.side == "LONG" else "buy"
                            rem_val = pos.qty_value * (1.0 - float(getattr(SETTINGS, "tp1_part", 0.5))) if pos.tp1_done else pos.qty_value
                            ok, _ = trader.close_reduce_market(sym_api, ro_side, value_qty=rem_val)
                            if ok:
                                om.close_all(symbol, "TP2"); send_msg(f"🎯 {symbol} TP2 — position clôturée")
                    continue

                # Pas de position -> décision & exécution
                if symbol not in om.pending_by_symbol:
                    dec: Decision = analyze_signal(price, df, {"score": score, **inst_merged}, macro=macro_data)
                    if dec.side == "NONE":
                        continue

                    # Dedup
                    try:
                        key_entry = round_price(symbol, dec.entry, meta, float(getattr(SETTINGS, "default_tick_size", 0.001)))
                        key_sl    = round_price(symbol, dec.sl,    meta, float(getattr(SETTINGS, "default_tick_size", 0.001)))
                        key_tp1   = round_price(symbol, dec.tp1,   meta, float(getattr(SETTINGS, "default_tick_size", 0.001)))
                        key_tp2   = round_price(symbol, dec.tp2,   meta, float(getattr(SETTINGS, "default_tick_size", 0.001)))
                        _key = f"{dec.side}:{key_entry}:{key_sl}:{key_tp1}:{key_tp2}"
                    except Exception:
                        _key = f"{dec.side}:{dec.entry}:{dec.sl}:{dec.tp1}:{dec.tp2}"
                    if _is_duplicate_signal(symbol, _key, int(getattr(SETTINGS, "symbol_cooldown_sec", 45))):
                        logger.info("Duplicate signal suppressed by cooldown", extra={"symbol": symbol})
                        continue

                    adv = should_cancel_or_requote("LONG" if dec.side == "LONG" else "SHORT", inst_merged, SETTINGS)
                    if adv != "OK" and bool(getattr(SETTINGS, "cancel_on_adverse", False)):
                        logger.info(f"block adverse={adv}", extra={"symbol": symbol})
                        continue

                    side = "buy" if dec.side == "LONG" else "sell"
                    entry_px = round_price(symbol, dec.entry, meta, float(getattr(SETTINGS, "default_tick_size", 0.001)))
                    px_maker = _tick_shift(symbol, entry_px, -1 if side == "buy" else +1, meta, float(getattr(SETTINGS, "default_tick_size", 0.001)))
                    px_maker = round_price(symbol, px_maker, meta, float(getattr(SETTINGS, "default_tick_size", 0.001)))

                    stage_fracs = [float(getattr(SETTINGS, "stage1_fraction", 0.35)), 1.0 - float(getattr(SETTINGS, "stage1_fraction", 0.35))] if bool(getattr(SETTINGS, "two_stage_entry", False)) else [1.0]

                    for i, frac in enumerate(stage_fracs):
                        oid = str(uuid.uuid4()) + f"-s{i+1}"
                        ok, res = _place_limit_with_lev_retry(
                            trader, sym_api, side, px_maker, oid,
                            post_only=bool(getattr(SETTINGS, "post_only_entries", True)),
                            logger=logger,
                            value_qty=float(getattr(SETTINGS, "margin_per_trade", 20.0)),
                            leverage=DEFAULT_LEVERAGE,
                        )
                        logger.info(f"[EXEC] side={dec.side} px={px_maker} stg={i+1}/{len(stage_fracs)} ok={ok} res={res} "
                                    f"score={_fmt(score)} dyn_req={_fmt(dyn_req)} comps={comps_ok}/{INST_COMPONENTS_MIN}",
                                    extra={"symbol": symbol})
                        if not ok:
                            break

                        om.add_pending(oid, symbol, side, px_maker)
                        om.open_position(symbol, dec.side, dec.entry, dec.sl, dec.tp1, dec.tp2)

                        comp_txt = (
                            f"sc={inst_merged.get('score', 0):.2f} | "
                            f"OI={inst_merged.get('oi_score', 0):.2f} "
                            f"Δ={inst_merged.get('delta_score', 0):.2f} "
                            f"CVD={int(inst_merged.get('delta_cvd_usd',0))} "
                            f"F={inst_merged.get('funding_score', 0):.2f} "
                            f"Liq={inst_merged.get('liq_new_score', inst_merged.get('liq_score', 0)):.2f}"
                        )
                        liq_src = inst_merged.get("liq_source", "-")
                        msg = (
                            f"🚀 {symbol} {dec.side} — stage {i+1}/{len(stage_fracs)} • post-only\n"
                            f"@ {px_maker} | SL {dec.sl:.5g} | TP1 {dec.tp1:.5g} | TP2 {dec.tp2:.5g}\n"
                            f"R:R min {float(getattr(SETTINGS,'req_rr_min',1.2)):.2f} • score {inst_merged.get('score',0):.2f}\n"
                            f"{comp_txt} • liq={liq_src}"
                        )
                        send_msg(msg)
                        last_trade_ts = time.time()

                        # Fenêtre de fill + re-quotes (courte, on reste dans le budget de la passe)
                        t0 = time.time()
                        rq = 0
                        while time.time() - t0 < float(getattr(SETTINGS, "entry_timeout_sec", 2.0)):
                            await asyncio.sleep(0.2)
                        while rq < int(getattr(SETTINGS, "max_requotes", 1)):
                            if time_budget_sec is not None and (time.time() - loop_started) > time_budget_sec:
                                logger.info("scan window done", extra={"symbol": symbol})
                                break
                            rq += 1
                            px_maker = _tick_shift(symbol, px_maker, +1 if side == 'buy' else -1, meta, float(getattr(SETTINGS, "default_tick_size", 0.001)))
                            px_maker = round_price(symbol, px_maker, meta, float(getattr(SETTINGS, "default_tick_size", 0.001)))
                            trader.cancel_by_client_oid(oid)
                            oid = str(uuid.uuid4()) + f"-rq{rq}"
                            ok, _ = _place_limit_with_lev_retry(
                                trader, sym_api, side, px_maker, oid,
                                post_only=bool(getattr(SETTINGS, "post_only_entries", True)),
                                logger=logger,
                                value_qty=float(getattr(SETTINGS, "margin_per_trade", 20.0)),
                                leverage=DEFAULT_LEVERAGE,
                            )
                            logger.info(f"REQUOTE {rq}/{int(getattr(SETTINGS,'max_requotes',1))} px={px_maker} ok={ok}", extra={"symbol": symbol})
                            if not ok:
                                break
                            om.add_pending(oid, symbol, side, px_maker)
                            t0 = time.time()
                            while time.time() - t0 < float(getattr(SETTINGS, "entry_timeout_sec", 2.0)):
                                await asyncio.sleep(0.2)

                        # Fallback IOC en fin de fenêtre si demandé
                        if bool(getattr(SETTINGS, "use_ioc_fallback", True)):
                            tick = float(meta.get(symbol, {}).get("tickSize", float(getattr(SETTINGS, "default_tick_size", 0.001))))
                            aggr_ticks = 50
                            ioc_px = round_price(symbol, entry_px + aggr_ticks * tick if side == "buy" else entry_px - aggr_ticks * tick, meta, tick)
                            ok, _ = _place_ioc_with_lev_retry(trader, sym_api, side, ioc_px, logger, value_qty=float(getattr(SETTINGS, "margin_per_trade", 20.0)))
                            logger.info(f"IOC tried ok={ok} px={ioc_px}", extra={"symbol": symbol})
                            if ok:
                                send_msg(f"⚡ {symbol} — IOC fallback déclenché (@ {ioc_px})")

            except Exception:
                logger.exception("run_symbol loop error", extra={"symbol": symbol})
                await asyncio.sleep(0.5)
    finally:
        # Nettoyage systématique des tâches et du callback WS
        for t in (task_agg, task_feed):
            try:
                t.cancel()
            except Exception:
                pass
        try:
            await asyncio.gather(task_agg, task_feed, return_exceptions=True)
        except Exception:
            pass
        if hasattr(kws, "off"):
            try:
                kws.off("order", on_order)
            except Exception:
                pass

# ====== Build universe (400 perp USDT), logs filtrés ======
def _quiet_noise_loggers():
    logging.getLogger("kucoin.trader").setLevel(logging.WARNING)
    logging.getLogger("kucoin.ws").setLevel(logging.WARNING)
    logging.getLogger("institutional_data").setLevel(logging.WARNING)

def _prioritize_xbt_btc(symbols: List[str]) -> List[str]:
    ordered = []
    if "XBTUSDT" in symbols:
        ordered.append("XBTUSDT")
    elif "BTCUSDT" in symbols:
        ordered.append("BTCUSDT")
    for s in symbols:
        if s not in ordered:
            ordered.append(s)
    return ordered

def _fallback_symbols_from_meta(target_max: int) -> List[str]:
    # Fallback robuste : on repart des métadonnées KuCoin Futures
    meta = fetch_symbol_meta()  # {display_symbol: {..., symbol_api, tickSize, ...}}
    syms = sorted([s for s in meta.keys() if s.endswith("USDT")])  # perp USDT
    # Intersection avec Binance (pour funding/liq)
    syms = [s for s in syms if map_symbol_to_binance(s)]
    syms = _prioritize_xbt_btc(syms)
    return syms[:target_max] if target_max > 0 else syms

def _build_symbols() -> List[str]:
    # Objectif : ~400 perp USDT
    # Si l’utilisateur fournit SYMBOLS (liste), on respecte et on ne touche pas.
    user_forced = os.getenv("SYMBOLS", "").strip() != ""
    target_max_env = int(os.getenv("SYMBOLS_MAX", "400"))
    target_max_cfg = int(getattr(SETTINGS, "symbols_max", target_max_env))
    target_max = max(400, target_max_cfg) if not user_forced else target_max_cfg  # vise 400 si pas de liste utilisateur

    excl = getattr(SETTINGS, "exclude_symbols", "")
    try:
        # 1) Essai via helper (intersection prête)
        common = common_usdt_symbols(limit=10_000, exclude_csv=excl)  # FIX: pas de 40 implicite
    except Exception:
        common = []
    # Dédup + tri alpha
    seen = set()
    common = [s for s in sorted(common) if not (s in seen or seen.add(s))]
    # Intersection Binance (sécurité pour funding/liq)
    common = [s for s in common if map_symbol_to_binance(s)]

    # Si ça renvoie trop peu (< 200), fallback méta
    if len(common) < 200:
        rootlog.info(f"[SYMS] helper renvoie {len(common)} syms → fallback via meta()")
        common = _fallback_symbols_from_meta(target_max*2)

    common = _prioritize_xbt_btc(common)
    return common[:target_max] if target_max > 0 else common

# ====== Worker séquentiel (défilement) ======
async def _worker_cyclic(worker_id: int, symbols: List[str], kws: KucoinPrivateWS, macro: 'MacroCache', meta: dict):
    total = len(symbols)
    while True:
        for idx, sym in enumerate(symbols, start=1):
            rootlog.info(f"[{worker_id}] → ({idx}/{total}) {sym}")
            await run_symbol(sym, kws, macro, meta, time_budget_sec=SCAN_TIME_PER_SYMBOL)

async def main():
    _quiet_noise_loggers()
    rootlog.info("Starting scanner...")

    # Univers de scan (400 par défaut, XBT/BTC first, alpha ensuite)
    if getattr(SETTINGS, "auto_symbols", True):
        symbols = _build_symbols()
        if getattr(SETTINGS, "symbols", None) and len(SETTINGS.symbols) > 0 and os.getenv("SYMBOLS", ""):
            # L'utilisateur a explicitement fourni une liste → on respecte.
            user_list = [s.strip().upper() for s in SETTINGS.symbols if s.strip()]
            user_list = sorted(set(user_list))
            SETTINGS.symbols = _prioritize_xbt_btc(user_list)
        else:
            SETTINGS.symbols = symbols

    n = len(SETTINGS.symbols)
    preview = ", ".join(SETTINGS.symbols[:40]) + (" ..." if n > 40 else "")
    rootlog.info(f"[SCAN] {n} paires prêtes (alpha, XBT/BTC first): {preview}")

    # Métadonnées / WS / Macro
    meta  = fetch_symbol_meta()
    macro = MacroCache()
    kws   = KucoinPrivateWS()
    asyncio.create_task(kws.run())

    # Toujours en mode séquentiel (défilement), jamais une tâche par symbole
    workers = max(1, int(SCAN_WORKERS))
    # sharding simple round-robin si workers > 1 (sinon défilement pur à 1 worker)
    shards = [[] for _ in range(workers)]
    for i, s in enumerate(SETTINGS.symbols):
        shards[i % workers].append(s)

    tasks = []
    for i in range(workers):
        tasks.append(asyncio.create_task(_worker_cyclic(i+1, shards[i], kws, macro, meta)))
        await asyncio.sleep(0.05)
    await asyncio.gather(*tasks)

if __name__ == "__main__":
    asyncio.run(main())
