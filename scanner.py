import asyncio, time, uuid
from collections import deque
import pandas as pd
import httpx

from config import SETTINGS
from institutional_aggregator import InstitutionalAggregator
from analyze_signal import analyze_signal, Decision
from kucoin_trader import KucoinTrader
from kucoin_ws import KucoinPrivateWS
from order_manager import OrderManager
from orderflow_features import compute_atr
from kucoin_utils import fetch_klines, fetch_symbol_meta, round_price, common_usdt_symbols
from telegram_notifier import send_msg
from institutional_data import get_macro_total_mcap, get_macro_total2, get_macro_btc_dominance
from adverse_selection import should_cancel_or_requote
from logger_utils import get_logger
from institutional_data import (
    get_open_interest, get_funding_rate, get_recent_liquidations, map_symbol_to_binance, get_liq_pack
)

rootlog = get_logger("scanner")

# ---------------------------------------------------------------------
# Config locaux & constantes
# ---------------------------------------------------------------------
INSTIT_DEBUG_EVERY_SEC = 0
LIQ_REFRESH_SEC = getattr(SETTINGS, "liq_refresh_sec", 30)

REQ_SCORE_MIN        = float(getattr(SETTINGS, "req_score_min", 2.2))
INST_COMPONENTS_MIN  = int(getattr(SETTINGS, "inst_components_min", 2))
OI_MIN               = float(getattr(SETTINGS, "oi_req_min", 0.40))
DELTA_MIN            = float(getattr(SETTINGS, "delta_req_min", 0.40))
FUND_MIN             = float(getattr(SETTINGS, "funding_req_min", 0.20))
LIQ_MIN              = float(getattr(SETTINGS, "liq_req_min", 0.50))
BOOK_MIN             = float(getattr(SETTINGS, "book_req_min", 0.30))
USE_BOOK             = bool(getattr(SETTINGS, "use_book_imbal", False))

PERSIST_WIN          = int(getattr(SETTINGS, "persist_win", 3))
PERSIST_MIN_OK       = int(getattr(SETTINGS, "persist_min_ok", 2))
SYMBOL_COOLDOWN_SEC  = int(getattr(SETTINGS, "symbol_cooldown_sec", 900))
MIN_LIQ_NORM         = float(getattr(SETTINGS, "min_liq_norm", 0.0))

BINANCE_BASE = "https://fapi.binance.com"
OI_FUND_REFRESH_SEC = int(getattr(SETTINGS, "oi_fund_refresh_sec", 45))
FUND_REF = float(getattr(SETTINGS, "funding_ref", 0.00025))
OI_DELTA_REF = float(getattr(SETTINGS, "oi_delta_ref", 0.02))
HTTP_TIMEOUT = float(getattr(SETTINGS, "http_timeout_sec", 6.0))

def _norm01(x: float, ref: float) -> float:
    if ref <= 0: return 0.0
    try: return max(0.0, min(1.0, float(x) / float(ref)))
    except Exception: return 0.0

def _fetch_oi_score_binance(symbol: str) -> float | None:
    """Score OI ∈ [0..1] via ΔOI% (5m)."""
    bsym = map_symbol_to_binance(symbol)
    try:
        r = httpx.get(
            f"{BINANCE_BASE}/futures/data/openInterestHist",
            params={"symbol": bsym, "period": "5m", "limit": 2},
            timeout=HTTP_TIMEOUT,
            headers={"Accept": "application/json"}
        )
        if r.status_code != 200: return None
        arr = r.json() or []
        if len(arr) < 2: return None
        a, b2 = arr[-2], arr[-1]
        oi1 = float(a.get("sumOpenInterest", a.get("openInterest", 0.0)) or 0.0)
        oi2 = float(b2.get("sumOpenInterest", b2.get("openInterest", 0.0)) or 0.0)
        if oi1 <= 0: return None
        delta_pct = abs((oi2 - oi1) / oi1)
        return _norm01(delta_pct, OI_DELTA_REF)
    except Exception:
        return None

def _fetch_funding_score_binance(symbol: str) -> float | None:
    """Score Funding ∈ [0..1] via |lastFundingRate| / FUND_REF."""
    try:
        r = get_funding_rate(symbol)
        if r is None: return None
        return _norm01(abs(float(r)), FUND_REF)
    except Exception:
        return None

# ---------------------------------------------------------------------
# Macro cache
# ---------------------------------------------------------------------
class MacroCache:
    def __init__(self): self.last=0; self.data={}
    def refresh(self):
        now=time.time()
        if now - self.last < getattr(SETTINGS, "macro_refresh_minutes", 5)*60: return self.data
        total=get_macro_total_mcap()
        total2=get_macro_total2() if getattr(SETTINGS, "use_total2", True) else 0.0
        dom=get_macro_btc_dominance()
        self.data={"TOTAL":total,"TOTAL2":total2,"BTC_DOM":dom, "TOTAL_PCT":0.0, "TOTAL2_PCT":0.0}
        self.last=now; return self.data

# ---------------------------------------------------------------------
# OHLC local 1m
# ---------------------------------------------------------------------
class OHLCV1m:
    def __init__(self, meta: dict):
        self.df = {}
        self.meta = meta  # display_symbol -> {symbol_api, tickSize, pricePrecision}

    def bootstrap(self, sym: str):
        logger = get_logger("scanner.bootstrap", sym)
        try:
            sym_api = self.meta[sym]["symbol_api"]
            df = fetch_klines(sym_api, granularity=1, limit=300)
            self.df[sym] = df
        except Exception as e:
            logger.warning(f"Bootstrap klines fallback: {e}", extra={"symbol": sym})
            now=int(time.time()*1000); base=50000.0
            rows=[{"time":now-(299-i)*60_000,"open":base,"high":base,"low":base,"close":base,"volume":0.0} for i in range(300)]
            self.df[sym]=pd.DataFrame(rows)

    def on_price(self, sym, price, ts, vol=0.0):
        if sym not in self.df: self.bootstrap(sym)
        df=self.df[sym]
        minute=(ts//60000)*60000
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
            new_row = {"time": minute,"open": last_close,"high": p,"low":  p,"close":p,"volume": float(vol)}
            self.df[sym]=pd.concat([df, pd.DataFrame([new_row])], ignore_index=True).tail(2000)

    def frame(self, sym):
        if sym not in self.df: self.bootstrap(sym)
        return self.df[sym]

# ---------------------------------------------------------------------
def _tick_shift(symbol: str, px: float, ticks: int, meta, default_tick: float) -> float:
    tick = float(meta.get(symbol,{}).get("tickSize", default_tick))
    return px + ticks * tick

# ---------------------------------------------------------------------
# Score global = SOMME pondérée
# ---------------------------------------------------------------------
def _compute_global_score_sum(inst: dict) -> float:
    w_oi   = float(getattr(SETTINGS, "w_oi", 1.0))
    w_fund = float(getattr(SETTINGS, "w_funding", 1.0))
    w_delta= float(getattr(SETTINGS, "w_delta", 1.0))
    w_liq  = float(getattr(SETTINGS, "w_liq", 1.0))
    w_book = float(getattr(SETTINGS, "w_book_imbal", 1.0))
    use_book = bool(getattr(SETTINGS, "use_book_imbal", USE_BOOK))

    total = 0.0
    def add(key: str, w: float):
        nonlocal total
        if w <= 0: return
        if key not in inst or inst[key] is None: return
        try: total += w * float(inst[key])
        except Exception: pass

    add("oi_score", w_oi)
    add("delta_score", w_delta)
    add("funding_score", w_fund)
    if "liq_new_score" in inst: add("liq_new_score", w_liq)
    elif "liq_score" in inst:   add("liq_score", w_liq)
    if use_book: add("book_imbal_score", w_book)
    return float(total)

def _components_ok(inst: dict) -> int:
    oi_ok    = float(inst.get("oi_score",0.0))    >= OI_MIN
    dlt_ok   = float(inst.get("delta_score",0.0)) >= DELTA_MIN
    fund_ok  = float(inst.get("funding_score",0.0))>= FUND_MIN
    liq_val  = float(inst.get("liq_new_score", inst.get("liq_score",0.0)))
    liq_ok   = liq_val >= LIQ_MIN
    if USE_BOOK:
        book_ok = float(inst.get("book_imbal_score",0.0)) >= BOOK_MIN
        return int(oi_ok)+int(dlt_ok)+int(fund_ok)+int(liq_ok)+int(book_ok)
    return int(oi_ok)+int(dlt_ok)+int(fund_ok)+int(liq_ok)

# --- seuil dynamique (débloque les signaux quand certains poids manquent) ---
def _active_weight_sum(inst: dict, w_oi: float, w_fund: float, w_dlt: float, w_liq: float, w_book: float, use_book: bool) -> float:
    s = 0.0
    if inst.get("oi_score")      is not None: s += w_oi
    if inst.get("funding_score") is not None: s += w_fund
    if inst.get("delta_score")   is not None: s += w_dlt
    if inst.get("liq_new_score") is not None or inst.get("liq_score") is not None: s += w_liq
    if use_book and inst.get("book_imbal_score") is not None: s += w_book
    return float(s)

def _dyn_req_score(inst: dict, w_cfg: tuple, use_book: bool) -> float:
    w_oi, w_fund, w_dlt, w_liq, w_book = w_cfg
    active = _active_weight_sum(inst, w_oi, w_fund, w_dlt, w_liq, w_book, use_book)
    base_req = float(getattr(SETTINGS, "req_score_min", 1.0))
    return max(0.0, min(base_req, active * 0.80))

# ---------------------------------------------------------------------
async def run_symbol(symbol: str, kws: KucoinPrivateWS, macro: MacroCache, meta: dict):
    logger = get_logger("scanner.symbol", symbol)
    w_cfg=(SETTINGS.w_oi, SETTINGS.w_funding, SETTINGS.w_delta, SETTINGS.w_liq, SETTINGS.w_book_imbal)
    agg=InstitutionalAggregator(symbol, w_cfg)
    trader=KucoinTrader()
    ohlc=OHLCV1m(meta)
    om=OrderManager()

    started_at = time.time()
    last_hb = 0.0
    last_liq_fetch = 0.0
    liq_pack_cache = {}
    last_trade_ts = 0.0

    last_oi_fund_fetch = 0.0
    oi_fund_cache = {}

    persist_buf = deque(maxlen=PERSIST_WIN)

    def on_order(msg):
        oid = msg.get("clientOid") or ""
        sym = msg.get("symbol","")
        status = msg.get("status","")
        avgp = None
        try: avgp = float(msg.get("avgFillPrice", msg.get("matchPrice", 0.0)) or 0.0)
        except: pass
        filled_value = None
        try: filled_value = float(msg.get("filledValue", 0.0))
        except: pass
        if oid:
            logger.debug(f"WS order event status={status} avgFill={avgp} filledValue={filled_value}", extra={"symbol": symbol})
            om.set_pending_status(oid, status, avg_fill_price=avgp, filled_value=filled_value)
            if status in ("filled","partialFilled","match") and sym:
                if avgp and sym in om.pos: om.update_entry_with_fill(sym, avgp)
            if status in ("filled","cancel"): om.remove_pending(oid)
    kws.on("order", on_order)

    async def feed_ohlc():
        while True:
            try:
                if agg.state.price is not None:
                    ohlc.on_price(symbol, agg.state.price, int(time.time()*1000), vol=abs(agg.state.delta))
                await asyncio.sleep(0.2)
            except Exception:
                logger.exception("feed_ohlc loop error", extra={"symbol": symbol})
                await asyncio.sleep(0.5)

    asyncio.create_task(agg.run())
    asyncio.create_task(feed_ohlc())
    logger.info("symbol task started", extra={"symbol": symbol})
    await asyncio.sleep(2.0)

    while True:
        try:
            await asyncio.sleep(1.1)
            score_from_agg, inst = agg.get_meta_score()
            df = ohlc.frame(symbol)
            price=float(df["close"].iloc[-1])
            macro_data = macro.refresh()

            # --- LIQ PACK (HTTP) ---
            if (time.time() - last_liq_fetch) > LIQ_REFRESH_SEC:
                last_liq_fetch = time.time()
                try:
                    liq_pack_cache = get_liq_pack(symbol)
                    if liq_pack_cache.get("liq_source") != "none":
                        logger.info(
                            f"[LIQ] src={liq_pack_cache.get('liq_source')} "
                            f"sc={liq_pack_cache.get('liq_new_score',0.0):.3f} "
                            f"N5m={liq_pack_cache.get('liq_notional_5m',0.0):.0f} "
                            f"imb={liq_pack_cache.get('liq_imbalance_5m',0.0):.3f} "
                            f"norm={liq_pack_cache.get('liq_norm',0.0):.0f}",
                            extra={"symbol": symbol}
                        )
                except Exception as e:
                    logger.exception(f"[LIQ PACK] fetch error: {e}", extra={"symbol": symbol})

            # --- OI/Funding (léger) ---
            if (time.time() - last_oi_fund_fetch) > OI_FUND_REFRESH_SEC:
                last_oi_fund_fetch = time.time()
                try:
                    oi_sc = _fetch_oi_score_binance(symbol)
                    if oi_sc is not None: oi_fund_cache["oi_score"] = float(oi_sc)
                    fund_sc = _fetch_funding_score_binance(symbol)
                    if fund_sc is not None: oi_fund_cache["funding_score"] = float(fund_sc)
                    if oi_fund_cache:
                        logger.info(
                            f"[OI/FUND] oi={oi_fund_cache.get('oi_score')} fund={oi_fund_cache.get('funding_score')}",
                            extra={"symbol": symbol}
                        )
                except Exception as e:
                    logger.exception(f"[OI/FUND ENRICH] error: {e}", extra={"symbol": symbol})

            # ---- merge inst + liq + oi/fund ----
            inst_merged = {**inst, **(liq_pack_cache or {}), **(oi_fund_cache or {})}

            # ---- Liquidity floor optionnel ----
            if MIN_LIQ_NORM > 0:
                liq_norm = float(inst_merged.get("liq_norm", 0.0) or 0.0)
                if liq_norm and liq_norm < MIN_LIQ_NORM:
                    if time.time() - last_hb > 30:
                        last_hb = time.time()
                        logger.info(f"hb illiq p={price:.4f} norm={liq_norm:.0f}", extra={"symbol": symbol})
                    continue

            # ---- Score global = SOMME pondérée ----
            score = _compute_global_score_sum(inst_merged)
            inst_merged["score"] = score

            # ---- Seuil dynamique + boost si composante très forte ----
            use_book = bool(getattr(SETTINGS, "use_book_imbal", False))
            dyn_req = _dyn_req_score(inst_merged, w_cfg, use_book)

            comps_ok = _components_ok(inst_merged)
            if comps_ok >= INST_COMPONENTS_MIN and score < dyn_req:
                score = dyn_req

            boost = 0.0
            liq_val = float(inst_merged.get("liq_new_score", inst_merged.get("liq_score", 0.0)) or 0.0)
            if liq_val >= max(0.75, LIQ_MIN): boost = max(boost, 0.25)
            if float(inst_merged.get("delta_score", 0.0)) >= max(0.75, DELTA_MIN): boost = max(boost, 0.20)
            if float(inst_merged.get("oi_score", 0.0))    >= max(0.75, OI_MIN):    boost = max(boost, 0.15)
            if float(inst_merged.get("funding_score", 0.0))>= max(0.75, FUND_MIN): boost = max(boost, 0.10)
            score += boost
            inst_merged["score"] = score

            # ---- Heartbeat 30s ----
            if time.time() - last_hb > 30:
                last_hb = time.time()
                logger.info(
                    f"hb p={price:.4f} s={score:.2f} oi={inst_merged.get('oi_score',0):.2f} "
                    f"dlt={inst_merged.get('delta_score',0):.2f} fund={inst_merged.get('funding_score',0):.2f} "
                    f"liq={liq_val:.2f}",
                    extra={"symbol": symbol}
                )

            # warmup
            if (time.time() - started_at) < getattr(SETTINGS, "warmup_seconds", 0):
                continue

            # ---- Gate + persistance ----
            gate_now = (score >= dyn_req) and (comps_ok >= INST_COMPONENTS_MIN)
            persist_buf.append(1 if gate_now else 0)
            if sum(persist_buf) < PERSIST_MIN_OK:
                continue

            # ---- Cooldown ----
            if (time.time() - last_trade_ts) < SYMBOL_COOLDOWN_SEC:
                continue

            # ---- Gestion position existante ----
            pos=om.pos.get(symbol)
            if pos:
                try: atr=float(compute_atr(df).iloc[-1])
                except Exception: atr=0.0
                if not pos.tp1_done:
                    if (pos.side=="LONG" and price>=pos.tp1) or (pos.side=="SHORT" and price<=pos.tp1):
                        ro_side="sell" if pos.side=="LONG" else "buy"
                        ok,_=trader.close_reduce_market(symbol, ro_side, value_qty=pos.qty_value*SETTINGS.tp1_part)
                        if ok:
                            om.close_half_at_tp1(symbol); send_msg(f"✅ {symbol} TP1 — BE")
                            logger.info("TP1 hit → BE", extra={"symbol": symbol})
                else:
                    trail=getattr(SETTINGS, "trail_mult_atr", 1.2)*float(atr)
                    if pos.side=="LONG":
                        pos.sl=max(pos.sl, price-trail)
                        if price<=pos.sl:
                            ok,_=trader.close_reduce_market(symbol,"sell", value_qty=pos.qty_value*(1.0-SETTINGS.tp1_part))
                            if ok: om.close_all(symbol,"TRAIL_LONG"); send_msg(f"🛑 {symbol} Trailing stop LONG")
                    else:
                        pos.sl=min(pos.sl, price+trail)
                        if price>=pos.sl:
                            ok,_=trader.close_reduce_market(symbol,"buy", value_qty=pos.qty_value*(1.0-SETTINGS.tp1_part))
                            if ok: om.close_all(symbol,"TRAIL_SHORT"); send_msg(f"🛑 {symbol} Trailing stop SHORT")

                if symbol in om.pos:
                    pos=om.pos[symbol]
                    if (pos.side=="LONG" and price>=pos.tp2) or (pos.side=="SHORT" and price<=pos.tp2):
                        ro_side="sell" if pos.side=="LONG" else "buy"
                        rem=pos.qty_value*(1.0-(getattr(SETTINGS,"tp1_part",0.5) if pos.tp1_done else 0.0))
                        ok,_=trader.close_reduce_market(symbol, ro_side, value_qty=rem)
                        if ok: om.close_all(symbol,"TP2"); send_msg(f"🎯 {symbol} TP2 — clôture")
                continue

            # ---- Pas de position/pending -> décision & exécution ----
            if symbol not in om.pending_by_symbol:
                dec: Decision = analyze_signal(price, df, {"score":score, **inst_merged}, macro=macro_data)
                if dec.side == "NONE":
                    logger.info(f"rej s={score:.2f} ok={comps_ok}/{INST_COMPONENTS_MIN}", extra={"symbol": symbol})
                    continue

                adv = should_cancel_or_requote("LONG" if dec.side=="LONG" else "SHORT", inst_merged, SETTINGS)
                if adv!="OK" and getattr(SETTINGS, "cancel_on_adverse", True):
                    logger.info(f"block adverse={adv}", extra={"symbol": symbol})
                    continue

                side="buy" if dec.side=="LONG" else "sell"
                entry_px = round_price(symbol, dec.entry, meta, getattr(SETTINGS,"default_tick_size", 0.001))
                px_maker = _tick_shift(symbol, entry_px, -1 if side=="buy" else +1, meta, getattr(SETTINGS,"default_tick_size", 0.001))
                px_maker = round_price(symbol, px_maker, meta, getattr(SETTINGS,"default_tick_size", 0.001))

                stage_fracs = [getattr(SETTINGS,"stage1_fraction",0.5), 1.0-getattr(SETTINGS,"stage1_fraction",0.5)] if getattr(SETTINGS,"two_stage_entry", False) else [1.0]
                for i, frac in enumerate(stage_fracs):
                    oid = str(uuid.uuid4())+f"-s{i+1}"
                    ok,res = trader.place_limit(symbol, side, px_maker, oid, post_only=getattr(SETTINGS,"post_only_entries", True))
                    logger.info(f"ENTRY {side} px={px_maker} stg={i+1}/{len(stage_fracs)} ok={ok}", extra={"symbol": symbol})
                    if not ok:
                        logger.error(f"ENTRY FAIL stage{i+1} resp={res}", extra={"symbol": symbol})
                        break

                    om.add_pending(oid, symbol, side, px_maker)
                    om.open_position(symbol, dec.side, dec.entry, dec.sl, dec.tp1, dec.tp2)
                    send_msg(f"🚀 {symbol} {dec.side} stage {i+1}/{len(stage_fracs)} post-only @ {px_maker}")
                    last_trade_ts = time.time()

                    # attente fill / re-quotes
                    t0=time.time(); rq=0
                    while time.time()-t0 < getattr(SETTINGS,"entry_timeout_sec", 6):
                        await asyncio.sleep(0.2)
                    while rq < getattr(SETTINGS,"max_requotes", 2):
                        rq += 1
                        px_maker = _tick_shift(symbol, px_maker, +1 if side=='buy' else -1, meta, getattr(SETTINGS,"default_tick_size", 0.001))
                        px_maker = round_price(symbol, px_maker, meta, getattr(SETTINGS,"default_tick_size", 0.001))
                        trader.cancel_by_client_oid(oid)
                        oid = str(uuid.uuid4())+f"-rq{rq}"
                        ok,_ = trader.place_limit(symbol, side, px_maker, oid, post_only=getattr(SETTINGS,"post_only_entries", True))
                        logger.info(f"REQUOTE {rq}/{getattr(SETTINGS,'max_requotes',2)} px={px_maker} ok={ok}", extra={"symbol": symbol})
                        if not ok: break
                        om.add_pending(oid, symbol, side, px_maker)
                        t0=time.time()
                        while time.time()-t0 < getattr(SETTINGS,"entry_timeout_sec", 6):
                            await asyncio.sleep(0.2)

                    if getattr(SETTINGS,"use_ioc_fallback", True):
                        ok,_ = trader.place_limit_ioc(symbol, side, entry_px)
                        logger.info(f"IOC tried ok={ok} px={entry_px}", extra={"symbol": symbol})
                        if ok: send_msg(f"⚡ {symbol} IOC fallback déclenché")

                    if i==0 and len(stage_fracs)==2:
                        await asyncio.sleep(0.8)
                        adv2 = should_cancel_or_requote("LONG" if dec.side=="LONG" else "SHORT", inst_merged, SETTINGS)
                        if adv2!="OK" and getattr(SETTINGS, "cancel_on_adverse", True):
                            logger.info(f"ABORT stage2 adverse={adv2}", extra={"symbol": symbol})
                            break
        except Exception:
            logger.exception("run_symbol loop error", extra={"symbol": symbol})
            await asyncio.sleep(0.5)

# ---------------------------------------------------------------------
async def main():
    rootlog.info("Starting scanner...")
    if getattr(SETTINGS, "auto_symbols", False):
        discovered = common_usdt_symbols(limit=0, exclude_csv=getattr(SETTINGS,"exclude_symbols",""))
        if discovered:
            SETTINGS.symbols = discovered
            rootlog.info(f"[SCAN] Auto-symbols activé — {len(discovered)} paires chargées.")
    kws=KucoinPrivateWS()
    meta = fetch_symbol_meta()
    macro=MacroCache()
    asyncio.create_task(kws.run())

    tasks = []
    for i, sym in enumerate(SETTINGS.symbols):
        tasks.append(asyncio.create_task(run_symbol(sym, kws, macro, meta)))
        await asyncio.sleep(0.05)
    await asyncio.gather(*tasks)

if __name__=="__main__":
    asyncio.run(main())
