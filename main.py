# --- robust sys.path patch (works without __init__.py) ---
import sys, pathlib, os
HERE = pathlib.Path(__file__).resolve().parent

CANDIDATES = [
    HERE,                     # /app or repo root
    HERE.parent,              # parent if main.py is in a subfolder
    HERE / "src",
    HERE / "app",
    HERE / "top1_institutional_bot_pro_plus",
    HERE / "top1_institutional_bot_400",
    HERE / "top1_institutional_bot",
    pathlib.Path("/app"),
]
added = False
for base in CANDIDATES:
    try:
        if (base / "core").exists():
            if str(base) not in sys.path:
                sys.path.insert(0, str(base))
            added = True
            break
    except Exception:
        pass
# Fallback: add HERE anyway (PEP 420 namespace packages)
if not added and str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))
# ---------------------------------------------------------

import os, asyncio, logging, contextlib
from typing import Dict, Any, List

from core.logging_setup import configure_logging
from core.ws_router import EventBus, PollingSource
from core.config import (
    AUTO_UNIVERSE, UNIVERSE_LIMIT, SYMBOLS as ENV_SYMS, WS_POLL_SEC,
    ORDER_VALUE_USDT, MAX_CONCURRENCY, H1_TTL_SEC, H4_TTL_SEC,
    ANALYZE_COOLDOWN_SEC, JITTER_SEC
)
from core.universe import load_universe
from data.market_cache import MarketCache
from core.scheduler import RoundRobinScheduler

from alpha.inst_model import InstAutoTune, decide_institutional
from alpha import analyzer
from execution.executor import smart_submit
from notify.telegram import send as tg

# WS realtime
from ws.venues import VenueFeed
from execution.sor import SOR
from execution.anti_adverse import AntiAdverse

configure_logging()
log = logging.getLogger("runner")

async def main():
    log.info("start", extra={"symbol":"-"})
    # Universe
    symbols = load_universe(AUTO_UNIVERSE, ENV_SYMS, UNIVERSE_LIMIT)
    log.info("universe size=%d", len(symbols), extra={"symbol":"-"})

    # Infra
    cache = MarketCache(H1_TTL_SEC, H4_TTL_SEC)
    tuner = InstAutoTune()
    sched = RoundRobinScheduler(ANALYZE_COOLDOWN_SEC, JITTER_SEC)
    sem = asyncio.Semaphore(MAX_CONCURRENCY)

    # Realtime infra
    feed = VenueFeed()
    sor = SOR()
    anti = AntiAdverse()

    # Bind WS handlers
    def on_trade(sym, d):
        # TODO: brancher CVD/liq si besoin
        pass

    def on_bt(sym, d):
        bid, ask = float(d.get("b", 0.0)), float(d.get("a", 0.0))
        # normalize symbol key if needed; here we use raw feed symbol as key
        sor.update(sym, "binance", bid, ask)

    feed.on("trade", on_trade)
    feed.on("bt", on_bt)

    async def run_ws():
        try:
            await feed.run_binance(symbols)
        except Exception as e:
            log.error("ws crash: %s", e, extra={"symbol":"-"})

    async def handle_symbol(sym: str):
        # 1) Institutional
        inst = decide_institutional(sym, tuner)
        score, req = float(inst["score"]), float(inst["req"])
        log.info(
            "[INST] score=%.3f req=%.2f | oi=%.3f fund=%.3f liq=%.3f cvd=%.1f sideBias=%s",
            score, req, float(inst["oi_s"]), float(inst["fr_s"]), float(inst["liq_s"]),
            float(inst["cvd"]), inst["side_bias"], extra={"symbol": sym}
        )
        if score < req:
            log.info("[INST] under gate -> skip", extra={"symbol": sym})
            sched.mark_ran(sym, bias=1.5)  # inst cooldown bias up if weak
            return

        # 2) Data (cached)
        df_h1 = cache.get_h1(sym)
        df_h4 = cache.get_h4(sym)
        if getattr(df_h1, "empty", False) or getattr(df_h4, "empty", False):
            log.info("[DATA] klines vides -> skip", extra={"symbol": sym})
            sched.mark_ran(sym, bias=1.2)
            return

        # 3) Decision
        res = analyzer.decide(sym, df_h1, df_h4, {"score": score})
        side = str(res.get("side", "none")).lower()
        if side not in ("long", "short"):
            side = "long"  # could map to inst sideBias here if needed
            res["side"] = side

        if not res.get("valid") or side not in ("long", "short"):
            log.info("[DECISION] no-trade: %s rr=%.2f", res.get("reason", "-"), float(res.get("rr", 0)),
                     extra={"symbol": sym})
            sched.mark_ran(sym, bias=1.1)
            return

        entry, sl, tp1, tp2 = map(float, (res["entry"], res["sl"], res["tp1"], res["tp2"]))
        log.info(
            "[DECISION] %s rr=%.2f entry=%s sl=%s tp1=%s tp2=%s",
            side.upper(), float(res.get("rr", 0)), entry, sl, tp1, tp2, extra={"symbol": sym}
        )

        # 4) Exec (via smart maker + SOR venue hint if extended)
        out = smart_submit(sym, side, entry, sl, tp1, tp2)
        code = (out.get("code") if isinstance(out, dict) else None)
        ok = bool(out.get("ok")) if isinstance(out, dict) else False
        oid = (out.get("data") or {}).get("orderId") if isinstance(out, dict) else None
        log.info("[EXEC-RESP] ok=%s code=%s orderId=%s body=%s",
                 ok, code, oid, str(out)[:220], extra={"symbol": sym})

        tg(
            f"🧠 *{sym}* — *{side.upper()}*\n"
            f"Entry: *{entry}* | SL: *{sl}* | TP1: *{tp1}* | TP2: *{tp2}*\n"
            f"Notional: *{ORDER_VALUE_USDT}* USDT | code: {code}"
        )

        # Adaptive cooldown: faster revisit if we just found a strong setup
        sched.mark_ran(sym, bias=0.6 if score > (req + 0.3) else 1.0)

    # launch WS in background
    asyncio.create_task(run_ws())

    # Round-robin init
    for s in symbols:
        sched.mark_ran(s)

    # Event bus
    bus = EventBus()
    bus.add_source(PollingSource(symbols, interval_sec=WS_POLL_SEC).__aiter__())
    await bus.start()

    async for ev in bus.events():
        sym = ev.get("symbol")
        if not sched.should_run(sym):
            continue

        async def _task(s=sym):
            async with sem:
                try:
                    await asyncio.to_thread(handle_symbol, s)
                except Exception as e:
                    log.error("symbol crash: %s", e, extra={"symbol": s})

        asyncio.create_task(_task())

if __name__ == "__main__":
    asyncio.run(main())
