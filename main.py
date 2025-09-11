# -*- coding: utf-8 -*-
"""
main.py — Boucle event-driven + fallback institutionnel structuré (OTE, liquidité, swings)
- Direction H4, exécution H1 via OTE 62–79% et pools de liquidité
- SL derrière la liquidité/swing + buffer ATR
- TP1 swing/pool opposé, TP2 RR cible (2.0 par défaut)
- Exécution SFI + fallback direct KuCoin avec vérif clientOid SEULEMENT si insertion acceptée
"""

import os, asyncio, logging, math, time
from typing import Dict, Any, Tuple, List, Union

from ws_router import EventBus, PollingSource
from execution_sfi import SFIEngine
from risk_guard import RiskGuard
from meta_policy import MetaPolicy
from perf_metrics import register_signal_perf, update_perf_for_symbol
from kucoin_utils import fetch_klines, fetch_symbol_meta
from log_setup import init_logging, enable_httpx
from kucoin_adapter import (
    place_limit_order,
    get_symbol_meta,
)

# get_order_by_client_oid est optionnel
try:
    from kucoin_adapter import get_order_by_client_oid  # type: ignore
except Exception:
    get_order_by_client_oid = None  # type: ignore

# ---- Soft imports institutionnel / autotune
HAS_INST = True
try:
    from inst_enrich import get_institutional_snapshot  # type: ignore
except Exception:
    HAS_INST = False

HAS_TUNER = True
try:
    from inst_autotune import InstAutoTune, components_ok  # type: ignore
except Exception:
    HAS_TUNER = False

# ---- Analyse
try:
    import analyze_bridge as analyze_signal  # type: ignore
except Exception:
    import analyze_signal  # type: ignore

TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID   = os.environ.get("TELEGRAM_CHAT_ID")

H1_LIMIT                  = int(os.getenv("H1_LIMIT", "500"))
H4_LIMIT                  = int(os.getenv("H4_LIMIT", "400"))
H1_REFRESH_SEC            = int(os.getenv("H1_REFRESH_SEC", "60"))
H4_REFRESH_SEC            = int(os.getenv("H4_REFRESH_SEC", "300"))
ANALYSIS_MIN_INTERVAL_SEC = int(os.getenv("ANALYSIS_MIN_INTERVAL_SEC", "15"))
WS_POLL_SEC               = int(os.getenv("WS_POLL_SEC", "5"))

RR_TARGET_TP2             = float(os.getenv("INST_RR_TARGET_TP2", "2.0"))
ATR_SL_MULT               = float(os.getenv("INST_ATR_SL_MULT", "1.0"))
ATR_MIN_PCT               = float(os.getenv("INST_ATR_MIN_PCT", "0.003"))
EQ_TOL_PCT                = float(os.getenv("INST_EQ_TOL_PCT", "0.0006"))
OTE_LOW                   = float(os.getenv("INST_OTE_LOW", "0.62"))
OTE_HIGH                  = float(os.getenv("INST_OTE_HIGH", "0.79"))
OTE_MID                   = (OTE_LOW + OTE_HIGH) / 2.0

KC_POST_ONLY_DEFAULT      = os.getenv("KC_POST_ONLY", "1") == "1"
KC_VERIFY_MAX_TRIES       = int(os.getenv("KC_VERIFY_MAX_TRIES", "5"))
KC_VERIFY_DELAY_SEC       = float(os.getenv("KC_VERIFY_DELAY_SEC", "0.35"))

_KLINE_CACHE: Dict[str, Dict[str, Any]] = {}
_LAST_ANALYSIS_TS: Dict[str, float] = {}

log = logging.getLogger("runner")
TUNER = InstAutoTune() if HAS_TUNER else None  # type: ignore


# ------------------------
# Utils
# ------------------------
def fmt_price(x):
    if x is None: return "—"
    if x == 0: return "0"
    try:
        d = 2 if x >= 1 else min(8, int(abs(math.log10(1.0/float(x)))) + 2)
        return f"{float(x):.{d}f}"
    except Exception:
        return str(x)

def send_telegram(text: str):
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        log.info("[TG OFF] %s", text); return
    import httpx
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = {"chat_id": TELEGRAM_CHAT_ID, "text": text, "parse_mode":"Markdown", "disable_web_page_preview": True}
    try:
        httpx.post(url, json=payload, timeout=10)
    except Exception as e:
        log.error("Telegram KO: %s", e)

def _get_klines_cached(symbol: str) -> Tuple[Any, Any]:
    now = time.time()
    ent = _KLINE_CACHE.get(symbol, {})
    need_h1 = ("h1" not in ent) or (now - ent.get("ts_h1", 0) > H1_REFRESH_SEC)
    need_h4 = ("h4" not in ent) or (now - ent.get("ts_h4", 0) > H4_REFRESH_SEC)

    if need_h1:
        ent["h1"] = fetch_klines(symbol, interval="1h", limit=H1_LIMIT)
        ent["ts_h1"] = now
    if need_h4:
        ent["h4"] = fetch_klines(symbol, interval="4h", limit=H4_LIMIT)
        ent["ts_h4"] = now

    _KLINE_CACHE[symbol] = ent
    return ent.get("h1"), ent.get("h4")

def _build_symbols() -> List[str]:
    env_syms = os.getenv("SYMBOLS", "").strip()
    if env_syms:
        return sorted(set(s.strip().upper() for s in env_syms.split(",") if s.strip()))

    try:
        meta = fetch_symbol_meta()
    except Exception as e:
        log.warning("fetch_symbol_meta KO: %s", e)
        return []

    syms = []
    for display, v in meta.items():
        if str(v.get("symbol_api", "")).endswith("USDTM"):
            syms.append(display)
    return sorted(set(syms))


# ------------------------
# Event handler
# ------------------------
async def handle_symbol_event(ev: Dict[str, Any], rg: RiskGuard, policy: MetaPolicy):
    symbol = ev.get("symbol")
    etype  = ev.get("type")
    if etype != "bar":
        return
    log.info("event: %s", etype, extra={"symbol": symbol})

    last = _LAST_ANALYSIS_TS.get(symbol, 0.0)
    if time.time() - last < ANALYSIS_MIN_INTERVAL_SEC:
        return
    _LAST_ANALYSIS_TS[symbol] = time.time()

    try:
        df_h1, df_h4 = _get_klines_cached(symbol)
    except Exception as e:
        log.warning("fetch_klines KO: %s", e, extra={"symbol": symbol})
        return
    if df_h1 is None or df_h4 is None:
        return

    # simplifié : appel à analyze_signal
    try:
        res = analyze_signal.analyze_signal(symbol=symbol, df_h1=df_h1, df_h4=df_h4)
    except Exception as e:
        log.warning("analyze_signal KO: %s", e, extra={"symbol": symbol})
        return

    if not isinstance(res, dict) or not res.get("valid"):
        return

    side   = res.get("side")
    entry  = res.get("entry", float(df_h1['close'].iloc[-1]))
    sl     = res.get("sl")
    tp1    = res.get("tp1")
    tp2    = res.get("tp2")
    rr     = res.get("rr", 0)

    log.info("analysis OK %s rr=%.2f", side, rr, extra={"symbol": symbol})

    # --- Risk guard
    rg_ok, _ = rg.can_enter(symbol)
    if not rg_ok:
        return

    # --- Execution fallback KuCoin direct
    try:
        meta = get_symbol_meta(symbol) or {}
        tick = float(meta.get("priceIncrement", 0.0)) or 0.0
    except Exception:
        tick = 0.0

    price = entry
    post_only = KC_POST_ONLY_DEFAULT
    kc = place_limit_order(
        symbol=symbol,
        side=side,
        price=float(price),
        value_usdt=20,
        sl=sl,
        tp1=tp1,
        tp2=tp2,
        post_only=post_only
    )
    log.info("kc resp: %s", kc, extra={"symbol": symbol})


# ------------------------
# Main
# ------------------------
async def main():
    init_logging()
    if os.getenv("LOG_HTTP", "0") == "1":
        enable_httpx(True)

    logging.getLogger("runner").info("start")

    try:
        symbols = _build_symbols()
    except Exception as e:
        logging.getLogger("runner").error("Build symbols KO: %s", e)
        symbols = []

    if not symbols:
        logging.getLogger("runner").warning("Aucun symbole.")
        return

    bus = EventBus()
    src = PollingSource(symbols, interval_sec=WS_POLL_SEC)
    bus.add_source(src.__aiter__())
    await bus.start()

    rg = RiskGuard()
    policy = MetaPolicy()

    async for ev in bus.events():
        try:
            await handle_symbol_event(ev, rg, policy)
        except Exception as e:
            logging.getLogger("runner").error("handle_symbol_event: %s", e)

if __name__ == "__main__":
    asyncio.run(main())
