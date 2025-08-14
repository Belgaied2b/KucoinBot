import asyncio, time, uuid
from collections import deque
import pandas as pd
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

# --- APIs externes déjà utilisées (aucun calcul local ajouté) ---
from institutional_data import (
    get_open_interest, get_funding_rate, get_recent_liquidations, map_symbol_to_binance,
    get_liq_pack
)

rootlog = get_logger("scanner")

# Debug périodique (mets à 0 pour off)
INSTIT_DEBUG_EVERY_SEC = 0
# Rafraîchissement du liq pack (HTTP throttle)
LIQ_REFRESH_SEC = getattr(SETTINGS, "liq_refresh_sec", 30)

# ---------------- Params par défaut (si absents dans SETTINGS) ----------------
# Important: comme on passe à une **SOMME pondérée**, un seuil autour de 2.0–2.2 est cohérent (si les poids ~1.0)
REQ_SCORE_MIN        = float(getattr(SETTINGS, "req_score_min", 2.2))
INST_COMPONENTS_MIN  = int(getattr(SETTINGS, "inst_components_min", 2))
OI_MIN               = float(getattr(SETTINGS, "oi_req_min", 0.40))
DELTA_MIN            = float(getattr(SETTINGS, "delta_req_min", 0.40))
FUND_MIN             = float(getattr(SETTINGS, "funding_req_min", 0.20))
LIQ_MIN              = float(getattr(SETTINGS, "liq_req_min", 0.50))
BOOK_MIN             = float(getattr(SETTINGS, "book_req_min", 0.30))
USE_BOOK             = bool(getattr(SETTINGS, "use_book_imbal", False))

# Persistance: 2 sur 3 fenêtres
PERSIST_WIN          = int(getattr(SETTINGS, "persist_win", 3))
PERSIST_MIN_OK       = int(getattr(SETTINGS, "persist_min_ok", 2))

# Cooldown par symbole (sec)
SYMBOL_COOLDOWN_SEC  = int(getattr(SETTINGS, "symbol_cooldown_sec", 900))

# Filtre d’activité min (optionnel, via liq_pack si dispo)
MIN_LIQ_NORM         = float(getattr(SETTINGS, "min_liq_norm", 0.0))  # 0 = désactivé

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

def _tick_shift(symbol: str, px: float, ticks: int, meta, default_tick: float) -> float:
    tick = float(meta.get(symbol,{}).get("tickSize", default_tick))
    return px + ticks * tick

# ------ CORRECTION MAJEURE: Score global = **SOMME** pondérée, pas moyenne ------
def _compute_global_score_sum(inst: dict) -> float:
    """
    Agrège **uniquement** les sous-scores déjà présents (provenant de tes sources existantes),
    en **somme pondérée**. AUCUN calcul local de métriques (pas d'analytique maison ici).
    Compatible avec req_score_min ≈ 2.0–2.2 si les poids ~1.0.
    """
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
        try:
            total += w * float(inst[key])
        except Exception:
            pass

    add("oi_score", w_oi)
    add("delta_score", w_delta)
    add("funding_score", w_fund)

    # Liquidity: priorité au nouveau score s'il est présent
    if "liq_new_score" in inst:
        add("liq_new_score", w_liq)
    elif "liq_score" in inst:
        add("liq_score", w_liq)

    if use_book:
        add("book_imbal_score", w_book)

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

    # Persistance locale 2/3 fenêtres (pas de calculs métriques, juste la gate)
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
            score_from_agg, inst = agg.get_meta_score()   # on garde inst tel quel (aucun calcul local ajouté)
            df = ohlc.frame(symbol)
            price=float(df["close"].iloc[-1])
            macro_data = macro.refresh()

            # --- Refresh liq pack (externe) ---
            if (time.time() - last_liq_fetch) > LIQ_REFRESH_SEC:
                last_liq_fetch = time.time()
                try:
                    liq_pack_cache = get_liq_pack(symbol)  # fournit liq_new_score, notional, imb, norm, etc.
                    # log compact
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

            # ---- merge inst avec liq pack (AUCUN recalcul de sous-scores ici) ----
            inst_merged = {**inst, **(liq_pack_cache or {})}

            # ---- Liquidity floor optionnel ----
            if MIN_LIQ_NORM > 0:
                liq_norm = float(inst_merged.get("liq_norm", 0.0) or 0.0)
                if liq_norm and liq_norm < MIN_LIQ_NORM:
                    if time.time() - last_hb > 30:
                        last_hb = time.time()
                        logger.info(f"hb illiq p={price:.4f} norm={liq_norm:.0f}", extra={"symbol": symbol})
                    continue

            # ---- CORRECTION: (re)score = **SOMME pondérée** sur les sous-scores disponibles ----
            score = _compute_global_score_sum(inst_merged)
            inst_merged["score"] = score

            # heartbeat 30s — affiche VRAI score (plus 0.01 bloqué)
            if time.time() - last_hb > 30:
                last_hb = time.time()
                logger.info(
                    f"hb p={price:.4f} s={score:.2f} oi={inst_merged.get('oi_score',0):.2f} "
                    f"dlt={inst_merged.get('delta_score',0):.2f} fund={inst_merged.get('funding_score',0):.2f} "
                    f"liq={inst_merged.get('liq_new_score', inst_merged.get('liq_score',0)):.2f}",
                    extra={"symbol": symbol}
                )

            # warmup
            if (time.time() - started_at) < getattr(SETTINGS, "warmup_seconds", 0):
                continue

            # ---- Gate “institutionnelle” + persistance 2/3 ----
            comps_ok = _components_ok(inst_merged)
            gate_now = (score >= REQ_SCORE_MIN) and (comps_ok >= INST_COMPONENTS_MIN)
            persist_buf.append(1 if gate_now else 0)
            if sum(persist_buf) < PERSIST_MIN_OK:
                continue

            # ---- Cooldown par symbole ----
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
                continue  # pas d'entrées si déjà en position

            # ---- Pas de position/pending -> décision & exécution ----
            if symbol not in om.pending_by_symbol:
                dec: Decision = analyze_signal(price, df, {"score":score, **inst_merged}, macro=macro_data)
                if dec.side == "NONE":
                    # log compact de rejet (pour diagnostiquer)
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
