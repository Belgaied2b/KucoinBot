# =====================================================================
# settings.py — Desk Lead Settings Loader
# Charge toutes les variables depuis l’environnement
# =====================================================================

import os


# ============================================================
# HELPERS
# ============================================================

def _get(key: str, default=None):
    v = os.getenv(key, default)
    if isinstance(v, str) and v.isdigit():
        return int(v)
    return v


def _get_float(key: str, default=None):
    try:
        return float(os.getenv(key, default))
    except:
        return default


def _get_bool(key: str, default="false"):
    return str(os.getenv(key, default)).lower() in ("1", "true", "yes", "on")


# ============================================================
# BITGET API
# ============================================================

API_KEY = os.getenv("API_KEY")
API_SECRET = os.getenv("API_SECRET")
API_PASSPHRASE = os.getenv("API_PASSPHRASE")

if not API_KEY:
    print("⚠️ WARNING: API_KEY missing")
if not API_SECRET:
    print("⚠️ WARNING: API_SECRET missing")
if not API_PASSPHRASE:
    print("⚠️ WARNING: API_PASSPHRASE missing")


# ============================================================
# TELEGRAM BOT
# ============================================================

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")

if not TELEGRAM_BOT_TOKEN:
    print("⚠️ WARNING: TELEGRAM_BOT_TOKEN missing")
if not TELEGRAM_CHAT_ID:
    print("⚠️ WARNING: TELEGRAM_CHAT_ID missing")


# ============================================================
# GLOBAL BOT SETTINGS
# ============================================================

ENV = os.getenv("ENV", "production")
TZ = os.getenv("TZ", "Europe/Paris")
SCAN_INTERVAL_MIN = _get("SCAN_INTERVAL_MIN", 5)

TOP_N_SYMBOLS = _get("TOP_N_SYMBOLS", 80)


# ============================================================
# RISK SETTINGS
# ============================================================

MARGIN_USDT = _get_float("MARGIN_USDT", 20.0)
LEVERAGE = _get_float("LEVERAGE", 10.0)
ACCOUNT_EQUITY_USDT = _get_float("ACCOUNT_EQUITY_USDT", 10000)
RISK_USDT = _get_float("RISK_USDT", 20.0)

MAX_GROSS_EXPOSURE = _get_float("MAX_GROSS_EXPOSURE", 2.0)
MAX_SYMBOL_EXPOSURE = _get_float("MAX_SYMBOL_EXPOSURE", 0.25)
CORR_GROUP_CAP = _get_float("CORR_GROUP_CAP", 0.5)
CORR_BTC_THRESHOLD = _get_float("CORR_BTC_THRESHOLD", 0.7)


# ============================================================
# STRUCTURE / MOMENTUM SETTINGS
# ============================================================

REQUIRE_STRUCTURE = _get_bool("REQUIRE_STRUCTURE", "true")
REQUIRE_MOMENTUM = _get_bool("REQUIRE_MOMENTUM", "true")
REQUIRE_HTF_ALIGN = _get_bool("REQUIRE_HTF_ALIGN", "true")
REQUIRE_BOS_QUALITY = _get_bool("REQUIRE_BOS_QUALITY", "true")

STRUCT_LOOKBACK = _get("STRUCT_LOOKBACK", 20)


# ============================================================
# RR PARAMETERS
# ============================================================

RR_MIN_STRICT = _get_float("RR_MIN_STRICT", 1.5)
RR_MIN_TOLERATED_WITH_INST = _get_float("RR_MIN_TOLERATED_WITH_INST", 1.20)

RR_MIN_DESK_PRIORITY = _get_float("RR_MIN_DESK_PRIORITY", 1.10)
RR_TARGET = _get_float("RR_TARGET", 1.6)

TP1_R_CLAMP_MIN = _get_float("TP1_R_CLAMP_MIN", 1.4)
TP1_R_CLAMP_MAX = _get_float("TP1_R_CLAMP_MAX", 1.6)
TP2_R_TARGET = _get_float("TP2_R_TARGET", 2.8)


# ============================================================
# SL PARAMETERS
# ============================================================

ATR_LEN = _get("ATR_LEN", 14)
ATR_MULT_SL = _get_float("ATR_MULT_SL", 2.5)
ATR_MULT_SL_CAP = _get_float("ATR_MULT_SL_CAP", 3.5)

SL_BUFFER_PCT = _get_float("SL_BUFFER_PCT", 0.0020)
SL_BUFFER_TICKS = _get("SL_BUFFER_TICKS", 3)
MIN_SL_TICKS = _get("MIN_SL_TICKS", 3)
MAX_SL_PCT = _get_float("MAX_SL_PCT", 0.07)

STOP_TRIGGER_TYPE_SL = os.getenv("STOP_TRIGGER_TYPE_SL", "MP")
STOP_TRIGGER_TYPE_TP = os.getenv("STOP_TRIGGER_TYPE_TP", "TP")


# ============================================================
# LIQUIDITY SETTINGS
# ============================================================

LIQ_LOOKBACK = _get("LIQ_LOOKBACK", 60)
LIQ_BUFFER_PCT = _get_float("LIQ_BUFFER_PCT", 0.0008)
LIQ_BUFFER_TICKS = _get("LIQ_BUFFER_TICKS", 3)


# ============================================================
# INSTITUTIONAL SETTINGS
# ============================================================

MIN_INST_SCORE = _get_float("MIN_INST_SCORE", 2.0)
COMMITMENT_MIN = _get_float("COMMITMENT_MIN", 0.55)

INST_SCORE_DESK_PRIORITY = _get_float("INST_SCORE_DESK_PRIORITY", 2)
COMMITMENT_DESK_PRIORITY = _get_float("COMMITMENT_DESK_PRIORITY", 0.60)

ENABLE_SQUEEZE_ENGINE = _get_bool("ENABLE_SQUEEZE_ENGINE", "true")
DESK_EV_MODE = _get_bool("DESK_EV_MODE", "true")


# ============================================================
# RETRY / NETWORK
# ============================================================

BINANCE_MIN_INTERVAL_S = _get_float("BINANCE_MIN_INTERVAL_S", 0.35)
BINANCE_HTTP_TIMEOUT_S = _get_float("BINANCE_HTTP_TIMEOUT_S", 7.0)
BINANCE_HTTP_RETRIES = _get("BINANCE_HTTP_RETRIES", 2)
BINANCE_SYMBOLS_TTL_S = _get("BINANCE_SYMBOLS_TTL_S", 900)

RETRY_300011_MAX = _get("RETRY_300011_MAX", 3)
RETRY_BACKOFF_MS_BASE = _get("RETRY_BACKOFF_MS_BASE", 250)
RETRY_BACKOFF_JITTER_MIN = _get("RETRY_BACKOFF_JITTER_MIN", 50)
RETRY_BACKOFF_JITTER_MAX = _get("RETRY_BACKOFF_JITTER_MAX", 200)


# ============================================================
# MISC
# ============================================================

FAIL_OPEN_TO_CORE = _get_bool("FAIL_OPEN_TO_CORE", "true")
PRICE_NUDGE_TICKS_MIN = _get("PRICE_NUDGE_TICKS_MIN", 0)
PRICE_NUDGE_TICKS_MAX = _get("PRICE_NUDGE_TICKS_MAX", 2)
SLIPPAGE_TICKS_LIMIT = _get("SLIPPAGE_TICKS_LIMIT", 2)
