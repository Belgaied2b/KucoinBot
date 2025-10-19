# main.py
"""
Cerveau: poll → institutional snapshot → fetch klines → décision → exécution KuCoin.
Logs Railway lisibles, erreurs explicites.
"""
import os, asyncio, logging, math, time
from typing import Dict, Any

from logging_setup import configure_logging
from ws_router import EventBus, PollingSource
from kucoin_adapter import place_limit_order, get_symbol_meta, fetch_klines
from inst_model import InstAutoTune, decide_institutional
from risk_guard import RiskGuard
import analyzer
from telegram_notifier import send as tg

configure_logging()
log = logging.getLogger("runner")

SYMBOLS = [s.strip().upper() for s in os.getenv("SYMBOLS","BTCUSDTM,ETHUSDTM,SOLUSDTM").split(",") if s.strip()]
ORDER_VALUE_USDT = float(os.getenv("ORDER_VALUE_USDT","20"))
POST_ONLY = os.getenv("KC_POST_ONLY","1").lower() in ("1","true","t","yes","on")
WS_POLL_SEC = float(os.getenv("WS_POLL_SEC","5"))

tuner = InstAutoTune()

def _round_to_tick(px: float, tick: float) -> float:
    if tick<=0: return float(px)
    return math.floor(float(px)/tick)*tick

async def handle_event(ev: Dict[str,Any], rg: RiskGuard):
    sym = ev.get("symbol"); et = ev.get("type")
    if et != "bar": return
    log.info("[EVENT] bar", extra={"symbol": sym})

    can, reason = rg.can_enter(sym)
    if not can:
        log.info("[RISK] blocked: %s", reason, extra={"symbol": sym})
        return

    # 1) Institutional snapshot + auto-tune gate
    try:
        inst = decide_institutional(sym, tuner)
        score, req = float(inst["score"]), float(inst["req"])
        log.info("[INST] score=%.3f req=%.2f | oi=%.3f fund=%.3f liq=%.3f cvd=%.1f sideBias=%s",
                 score, req, float(inst["oi_s"]), float(inst["fr_s"]), float(inst["liq_s"]),
                 float(inst["cvd"]), inst["side_bias"], extra={"symbol": sym})
        if score < req:
            log.info("[INST] under gate -> skip", extra={"symbol": sym})
            return
    except Exception as e:
        log.error("[INST] crash: %s", e, extra={"symbol": sym})
        return

    # 2) Market data
    try:
        df_h1 = fetch_klines(sym, "1h", 500)
        df_h4 = fetch_klines(sym, "4h", 400)
        if getattr(df_h1,"empty",False) or getattr(df_h4,"empty",False):
            log.info("[DATA] klines vides -> skip", extra={"symbol": sym}); return
    except Exception as e:
        log.error("[DATA] fetch KO: %s", e, extra={"symbol": sym}); return

    # 3) Decision technique (calée sur biais insti si tie-breaker)
    try:
        res = analyzer.decide(sym, df_h1, df_h4, {"score": score})
        side = str(res.get("side","none")).lower()
        if side not in ("long","short"):
            side = "long" if inst["side_bias"] == "long" else "short"
            res["side"] = side
    except Exception as e:
        log.error("[ANALYZE] crash: %s", e, extra={"symbol": sym}); return

    if not res.get("valid") or side not in ("long","short"):
        log.info("[DECISION] no-trade: %s rr=%.2f", res.get("reason","-"), float(res.get("rr",0)), extra={"symbol": sym})
        return

    entry, sl, tp1, tp2 = map(float, (res["entry"], res["sl"], res["tp1"], res["tp2"]))        
    log.info("[DECISION] %s rr=%.2f entry=%s sl=%s tp1=%s tp2=%s",
             side.upper(), float(res.get("rr",0)), entry, sl, tp1, tp2, extra={"symbol": sym})

    # 4) Execution KuCoin (LIMIT valueQty)
    try:
        meta = get_symbol_meta(sym) or {}
        tick = float(meta.get("priceIncrement",0) or 0)
    except Exception:
        tick = 0.0
    entry_px = _round_to_tick(entry, tick)
    side_api = "buy" if side=="long" else "sell"

    log.info("[EXEC] LIMIT %s px=%s notional=%s postOnly=%s", side_api, entry_px, ORDER_VALUE_USDT, POST_ONLY, extra={"symbol": sym})
    out = place_limit_order(sym, side_api, entry_px, ORDER_VALUE_USDT, post_only=POST_ONLY, sl=sl, tp1=tp1, tp2=tp2)
    code = (out.get("code") if isinstance(out,dict) else None)
    ok   = bool(out.get("ok")) if isinstance(out,dict) else False
    oid  = (out.get("data") or {}).get("orderId") if isinstance(out,dict) else None
    log.info("[EXEC-RESP] ok=%s code=%s orderId=%s body=%s", ok, code, oid, str(out)[:220], extra={"symbol": sym})

    # 5) Telegram
    tg(f"🧠 *{sym}* — *{side.upper()}*\n"
       f"Entry: *{entry_px}* | SL: *{sl}* | TP1: *{tp1}* | TP2: *{tp2}*\n"
       f"Notional: *{ORDER_VALUE_USDT}* USDT | code: {code}")

async def main():
    log.info("start")
    rg = RiskGuard()
    bus = EventBus()
    bus.add_source(PollingSource(SYMBOLS, interval_sec=WS_POLL_SEC).__aiter__())
    await bus.start()
    async for ev in bus.events():
        try:
            await handle_event(ev, rg)
        except Exception as e:
            log.error("handle_event crash: %s", e)

if __name__ == "__main__":
    asyncio.run(main())
