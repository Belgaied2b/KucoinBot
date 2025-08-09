import asyncio, time, uuid
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

class MacroCache:
    def __init__(self): self.last=0; self.data={}
    def refresh(self):
        now=time.time()
        if now - self.last < SETTINGS.macro_refresh_minutes*60: return self.data
        total=get_macro_total_mcap()
        total2=get_macro_total2() if SETTINGS.use_total2 else 0.0
        dom=get_macro_btc_dominance()
        self.data={"TOTAL":total,"TOTAL2":total2,"BTC_DOM":dom, "TOTAL_PCT":0.0, "TOTAL2_PCT":0.0}
        self.last=now; return self.data

class OHLCV1m:
    def __init__(self): self.df={}
    def bootstrap(self, sym):
        try:
            df=fetch_klines(sym, granularity=1, limit=300); self.df[sym]=df
        except:
            now=int(time.time()*1000); base=50000.0
            rows=[{"time":now-(299-i)*60_000,"open":base,"high":base,"low":base,"close":base,"volume":0.0} for i in range(300)]
            self.df[sym]=pd.DataFrame(rows)
    def on_price(self, sym, price, ts, vol=0.0):
        if sym not in self.df: self.bootstrap(sym)
        df=self.df[sym]
        minute=(ts//60000)*60000
        if int(df.iloc[-1]["time"]) == minute:
            idx = df.index[-1]
            current_high = float(df.at[idx, "high"])
            current_low  = float(df.at[idx, "low"])
            current_vol  = float(df.at[idx, "volume"])
            p = float(price)
            df.at[idx, "close"]  = p
            df.at[idx, "high"]   = max(current_high, p)
            df.at[idx, "low"]    = min(current_low,  p)
            df.at[idx, "volume"] = current_vol + float(vol)
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

async def run_symbol(symbol: str, kws: KucoinPrivateWS, macro: MacroCache, meta: dict):
    w_cfg=(SETTINGS.w_oi, SETTINGS.w_funding, SETTINGS.w_delta, SETTINGS.w_liq, SETTINGS.w_book_imbal)
    agg=InstitutionalAggregator(symbol, w_cfg)
    trader=KucoinTrader()
    ohlc=OHLCV1m()
    om=OrderManager()

    started_at = time.time()  # warmup timer

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
            om.set_pending_status(oid, status, avg_fill_price=avgp, filled_value=filled_value)
            if status in ("filled","partialFilled","match") and sym:
                if avgp and sym in om.pos: om.update_entry_with_fill(sym, avgp)
            if status in ("filled","cancel"): om.remove_pending(oid)
    kws.on("order", on_order)

    async def feed_ohlc():
        while True:
            if agg.state.price is not None:
                ohlc.on_price(symbol, agg.state.price, int(time.time()*1000), vol=abs(agg.state.delta))
            await asyncio.sleep(0.2)

    asyncio.create_task(agg.run())
    asyncio.create_task(feed_ohlc())
    await asyncio.sleep(2.0)

    while True:
        await asyncio.sleep(1.1)
        score, inst = agg.get_meta_score()
        df = ohlc.frame(symbol)
        price=float(df["close"].iloc[-1])
        macro_data = macro.refresh()

        # --- Warm-up: on évite de spammer & d’entrer avant que le flux ne soit “réel” ---
        warm = (time.time() - started_at) < SETTINGS.warmup_seconds
        if warm:
            # ne pas logguer “Score insuffisant” pendant warmup
            # et ne pas générer d'ordres
            continue

        pos=om.pos.get(symbol)
        if pos:
            atr=compute_atr(df).iloc[-1]
            if not pos.tp1_done:
                if (pos.side=="LONG" and price>=pos.tp1) or (pos.side=="SHORT" and price<=pos.tp1):
                    ro_side="sell" if pos.side=="LONG" else "buy"
                    ok,_=trader.close_reduce_market(symbol, ro_side, value_qty=pos.qty_value*SETTINGS.tp1_part)
                    if ok: om.close_half_at_tp1(symbol); send_msg(f"✅ {symbol} TP1 — BE")
            else:
                trail=SETTINGS.trail_mult_atr*float(atr)
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
                    rem=pos.qty_value*(1.0-(SETTINGS.tp1_part if pos.tp1_done else 0.0))
                    ok,_=trader.close_reduce_market(symbol, ro_side, value_qty=rem)
                    if ok: om.close_all(symbol,"TP2"); send_msg(f"🎯 {symbol} TP2 — clôture")

        if symbol not in om.pos and symbol not in om.pending_by_symbol:
            dec: Decision = analyze_signal(price, df, {"score":score, **inst}, macro=macro_data)
            if dec.side=="NONE":
                # on log moins verbeux (toutes les X sec côté Railway)
                if SETTINGS.log_signals and int(time.time()) % 10 == 0:
                    print(f"[{symbol}] score={score:.2f} :: {dec.name} / {dec.reason}")
                continue

            adv = should_cancel_or_requote("LONG" if dec.side=="LONG" else "SHORT", inst, SETTINGS)
            if adv!="OK" and SETTINGS.cancel_on_adverse:
                print(f"[{symbol}] ENTRY BLOCKED ({adv})"); continue

            side="buy" if dec.side=="LONG" else "sell"
            entry_px = round_price(symbol, dec.entry, meta, SETTINGS.default_tick_size)
            px_maker = _tick_shift(symbol, entry_px, -1 if side=="buy" else +1, meta, SETTINGS.default_tick_size)
            px_maker = round_price(symbol, px_maker, meta, SETTINGS.default_tick_size)

            stage_fracs = [SETTINGS.stage1_fraction, 1.0-SETTINGS.stage1_fraction] if SETTINGS.two_stage_entry else [1.0]
            for i, frac in enumerate(stage_fracs):
                oid = str(uuid.uuid4())+f"-s{i+1}"
                ok,res = trader.place_limit(symbol, side, px_maker, oid, post_only=SETTINGS.post_only_entries)
                if not ok:
                    print(f"[{symbol}] ENTRY FAIL stage{i+1} {res}")
                    break

                om.add_pending(oid, symbol, side, px_maker)
                om.open_position(symbol, dec.side, dec.entry, dec.sl, dec.tp1, dec.tp2)
                send_msg(f"🚀 {symbol} {dec.side} stage {i+1}/{len(stage_fracs)} post-only @ {px_maker}")

                t0=time.time(); rq=0
                while time.time()-t0 < SETTINGS.entry_timeout_sec:
                    await asyncio.sleep(0.2)
                while rq < SETTINGS.max_requotes:
                    rq += 1
                    px_maker = _tick_shift(symbol, px_maker, +1 if side=='buy' else -1, meta, SETTINGS.default_tick_size)
                    px_maker = round_price(symbol, px_maker, meta, SETTINGS.default_tick_size)
                    trader.cancel_by_client_oid(oid)
                    oid = str(uuid.uuid4())+f"-rq{rq}"
                    ok,_ = trader.place_limit(symbol, side, px_maker, oid, post_only=SETTINGS.post_only_entries)
                    if not ok: break
                    om.add_pending(oid, symbol, side, px_maker)
                    t0=time.time()
                    while time.time()-t0 < SETTINGS.entry_timeout_sec:
                        await asyncio.sleep(0.2)

                if SETTINGS.use_ioc_fallback:
                    ok,_ = trader.place_limit_ioc(symbol, side, entry_px)
                    if ok: send_msg(f"⚡ {symbol} IOC fallback déclenché")

                if i==0 and len(stage_fracs)==2:
                    await asyncio.sleep(0.8)
                    adv2 = should_cancel_or_requote("LONG" if dec.side=="LONG" else "SHORT", inst, SETTINGS)
                    if adv2!="OK" and SETTINGS.cancel_on_adverse:
                        print(f"[{symbol}] ABORT stage2 ({adv2})"); break

async def main():
    # Auto-discovery des symboles si activé
    if SETTINGS.auto_symbols:
        discovered = common_usdt_symbols(limit=SETTINGS.symbols_max, exclude_csv=SETTINGS.exclude_symbols)
        if discovered:
            SETTINGS.symbols = discovered
            print(f"[SCAN] Auto-symbols activé — {len(discovered)} paires chargées.")
    kws=KucoinPrivateWS()
    meta = fetch_symbol_meta()
    macro=MacroCache()
    asyncio.create_task(kws.run())
    await asyncio.gather(*(run_symbol(sym, kws, macro, meta) for sym in SETTINGS.symbols))

if __name__=="__main__":
    asyncio.run(main())
