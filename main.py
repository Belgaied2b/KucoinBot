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

# get_order_by_client_oid est optionnel : pas d'ImportError si absent
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

# ---- Analyse: bridge prioritaire, sinon fallback
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

# Cibles / buffers institutionnels
RR_TARGET_TP2             = float(os.getenv("INST_RR_TARGET_TP2", "2.0"))
ATR_SL_MULT               = float(os.getenv("INST_ATR_SL_MULT", "1.0"))     # buffer ajouté derrière le swing/liquidité
ATR_MIN_PCT               = float(os.getenv("INST_ATR_MIN_PCT", "0.003"))   # fallback ATR min = 0.3% prix
EQ_TOL_PCT                = float(os.getenv("INST_EQ_TOL_PCT", "0.0006"))   # tolérance equal highs/lows (0.06%)
OTE_LOW                   = float(os.getenv("INST_OTE_LOW", "0.62"))
OTE_HIGH                  = float(os.getenv("INST_OTE_HIGH", "0.79"))
OTE_MID                   = (OTE_LOW + OTE_HIGH) / 2.0

# Fallback KuCoin
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
    for attempt in (1, 2):
        try:
            resp = httpx.post(url, json=payload, timeout=10)
            if resp.status_code == 200 and (resp.json().get("ok") is True):
                log.info("Telegram OK (len=%s)", len(text)); return
            else:
                log.warning("Telegram HTTP=%s body=%s (attempt %s)", resp.status_code, resp.text[:200], attempt)
        except Exception as e:
            log.error("Telegram KO: %s (attempt %s)", e, attempt)

def _get_klines_cached(symbol: str) -> Tuple[Any, Any]:
    now = time.time()
    ent = _KLINE_CACHE.get(symbol, {})
    need_h1 = ("h1" not in ent) or (now - ent.get("ts_h1", 0) > H1_REFRESH_SEC)
    need_h4 = ("h4" not in ent) or (now - ent.get("ts_h4", 0) > H4_REFRESH_SEC)

    if need_h1:
        ent["h1"] = fetch_klines(symbol, interval="1h", limit=H1_LIMIT)
        ent["ts_h1"] = now
        log.debug("H1 fetch", extra={"symbol": symbol})
    else:
        log.debug("H1 cache hit", extra={"symbol": symbol})

    if need_h4:
        ent["h4"] = fetch_klines(symbol, interval="4h", limit=H4_LIMIT)
        ent["ts_h4"] = now
        log.debug("H4 fetch", extra={"symbol": symbol})
    else:
        log.debug("H4 cache hit", extra={"symbol": symbol})

    _KLINE_CACHE[symbol] = ent
    return ent.get("h1"), ent.get("h4")

def _build_symbols() -> List[str]:
    """
    Construit la liste des symboles à scanner.
    - Si $SYMBOLS est défini → utilise cette liste.
    - Sinon → fetch_symbol_meta() et renvoie les versions display (ex: BTCUSDT).
    """
    env_syms = os.getenv("SYMBOLS", "").strip()
    if env_syms:
        lst = [s.strip().upper() for s in env_syms.split(",") if s.strip()]
        return sorted(set(lst))

    try:
        meta = fetch_symbol_meta()
    except Exception as e:
        log.warning("fetch_symbol_meta KO: %s", e)
        return []

    syms: List[str] = []
    for display, v in meta.items():
        sym_api = str(v.get("symbol_api", "")).strip().upper()
        if sym_api.endswith("USDTM"):
            syms.append(display)

    if not syms:
        log.warning("Aucun symbole USDTM trouvé dans fetch_symbol_meta()")

    return sorted(set(syms))

# ------------------------
# Exécution robuste SFI
# ------------------------
# (… reste du code identique : _normalize_orders, _safe_place_orders,
# outils institutionnels, handle_symbol_event, et boucle main)
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
