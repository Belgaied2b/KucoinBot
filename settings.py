# ============================================================
# settings.py — Bitget Desk Lead Settings
# Chargement intelligent des variables d’environnement
# (convertit automatiquement en int, float, bool)
# ============================================================

import os


# ------------------------------
# Helpers
# ------------------------------
def _get(key, default=None):
    return os.getenv(key, default)

def _to_bool(v: str) -> bool:
    return str(v).lower() in ("1", "true", "yes", "y", "on")

def _to_int(v: str) -> int:
    try:
        return int(v)
    except:
        return int(float(v))

def _to_float(v: str) -> float:
    try:
        return float(v)
    except:
        return float(v.replace(",", "."))


# ============================================================
# TELEGRAM / ENV
# ============================================================
TOKEN = _get("TELEGRAM_BOT_TOKEN")
ENV = _get("ENV", "production")
TZ = _get("TZ", "Europe/Paris")
DRY_RUN = _to_bool(_get("DRY_RUN", "false"))


# ============================================================
# RISK / ORDER SETTINGS
# ============================================================
MARGIN_USDT = _to_float(_get("MARGIN_USDT", "20"))
LEVERAGE = _to_int(_get("LEVERAGE", "10"))
SCAN_INTERVAL_MIN = _to_int(_get("SCAN_INTERVAL_MIN", "5"))
MAX_ORDERS_PER_SCAN = _to_int(_get("MAX_ORDERS_PER_SCAN", "5"))

MIN_INST_SCORE = _to_int(_get("MIN_INST_SCORE", "2"))

REQUIRE_STRUCTURE = _to_bool(_get("REQUIRE_STRUCTURE", "true"))
REQUIRE_MOMENTUM = _to_bool(_get("REQUIRE_MOMENTUM", "true"))
REQUIRE_HTF_ALIGN = _to_bool(_get("REQUIRE_HTF_ALIGN", "true"))
REQUIRE_BOS_QUALITY = _to_bool(_get("REQUIRE_BOS_QUALITY", "true"))

RR_MIN_STRICT = _to_float(_get("RR_MIN_STRICT", "1.5"))
RR_MIN_TOLERATED_WITH_INST = _to_float(_get("RR_MIN_TOLERATED_WITH_INST", "1.20"))
COMMITMENT_MIN = _to_float(_get("COMMITMENT_MIN", "0.55"))

DESK_EV_MODE = _to_bool(_get("DESK_EV_MODE", "true"))
RR_MIN_DESK_PRIORITY = _to_float(_get("RR_MIN_DESK_PRIORITY", "1.10"))
INST_SCORE_DESK_PRIORITY = _to_int(_get("INST_SCORE_DESK_PRIORITY", "2"))
COMMITMENT_DESK_PRIORITY = _to_float(_get("COMMITMENT_DESK_PRIORITY", "0.60"))

STOP_TRIGGER_TYPE_SL = _get("STOP_TRIGGER_TYPE_SL", "MP")
STOP_TRIGGER_TYPE_TP = _get("STOP_TRIGGER_TYPE_TP", "TP")

ATR_LEN = _to_int(_get("ATR_LEN", "14"))
ATR_MULT_SL = _to_float(_get("ATR_MULT_SL", "2.5"))
ATR_MULT_SL_CAP = _to_float(_get("ATR_MULT_SL_CAP", "3.5"))

SL_BUFFER_PCT = _to_float(_get("SL_BUFFER_PCT", "0.0020"))
SL_BUFFER_TICKS = _to_int(_get("SL_BUFFER_TICKS", "3"))
MIN_SL_TICKS = _to_int(_get("MIN_SL_TICKS", "3"))
MAX_SL_PCT = _to_float(_get("MAX_SL_PCT", "0.07"))

STRUCT_LOOKBACK = _to_int(_get("STRUCT_LOOKBACK", "20"))
BE_FEE_BUFFER_TICKS = _to_int(_get("BE_FEE_BUFFER_TICKS", "1"))

TP1_R_CLAMP_MIN = _to_float(_get("TP1_R_CLAMP_MIN", "1.4"))
TP1_R_CLAMP_MAX = _to_float(_get("TP1_R_CLAMP_MAX", "1.6"))
TP2_R_TARGET = _to_float(_get("TP2_R_TARGET", "2.8"))
MIN_TP_TICKS = _to_int(_get("MIN_TP_TICKS", "1"))
TP1_R_BY_VOL = _to_bool(_get("TP1_R_BY_VOL", "true"))

VOL_REGIME_ATR_PCT_LOW = _to_float(_get("VOL_REGIME_ATR_PCT_LOW", "0.015"))
VOL_REGIME_ATR_PCT_HIGH = _to_float(_get("VOL_REGIME_ATR_PCT_HIGH", "0.035"))

ACCOUNT_EQUITY_USDT = _to_float(_get("ACCOUNT_EQUITY_USDT", "10000"))
MAX_GROSS_EXPOSURE = _to_float(_get("MAX_GROSS_EXPOSURE", "2.0"))
MAX_SYMBOL_EXPOSURE = _to_float(_get("MAX_SYMBOL_EXPOSURE", "0.25"))
CORR_GROUP_CAP = _to_float(_get("CORR_GROUP_CAP", "0.5"))
CORR_BTC_THRESHOLD = _to_float(_get("CORR_BTC_THRESHOLD", "0.7"))

RISK_USDT = _to_float(_get("RISK_USDT", "20"))
RR_TARGET = _to_float(_get("RR_TARGET", "1.6"))
TOP_N_SYMBOLS = _to_int(_get("TOP_N_SYMBOLS", "80"))

ENABLE_SQUEEZE_ENGINE = _to_bool(_get("ENABLE_SQUEEZE_ENGINE", "true"))
FAIL_OPEN_TO_CORE = _to_bool(_get("FAIL_OPEN_TO_CORE", "true"))

# ============================================================
# BINANCE PARAMETERS (Institutional Data)
# ============================================================
BINANCE_MIN_INTERVAL_S = _to_float(_get("BINANCE_MIN_INTERVAL_S", "0.35"))
BINANCE_HTTP_TIMEOUT_S = _to_float(_get("BINANCE_HTTP_TIMEOUT_S", "7.0"))
BINANCE_HTTP_RETRIES = _to_int(_get("BINANCE_HTTP_RETRIES", "2"))
BINANCE_SYMBOLS_TTL_S = _to_int(_get("BINANCE_SYMBOLS_TTL_S", "900"))

RETRY_300011_MAX = _to_int(_get("RETRY_300011_MAX", "3"))
RETRY_BACKOFF_MS_BASE = _to_int(_get("RETRY_BACKOFF_MS_BASE", "250"))
RETRY_BACKOFF_JITTER_MIN = _to_int(_get("RETRY_BACKOFF_JITTER_MIN", "50"))
RETRY_BACKOFF_JITTER_MAX = _to_int(_get("RETRY_BACKOFF_JITTER_MAX", "200"))

PRICE_NUDGE_TICKS_MIN = _to_int(_get("PRICE_NUDGE_TICKS_MIN", "0"))
PRICE_NUDGE_TICKS_MAX = _to_int(_get("PRICE_NUDGE_TICKS_MAX", "2"))

SLIPPAGE_TICKS_LIMIT = _to_int(_get("SLIPPAGE_TICKS_LIMIT", "2"))
LIQ_LOOKBACK = _to_int(_get("LIQ_LOOKBACK", "60"))
LIQ_BUFFER_PCT = _to_float(_get("LIQ_BUFFER_PCT", "0.0008"))
LIQ_BUFFER_TICKS = _to_int(_get("LIQ_BUFFER_TICKS", "3"))
