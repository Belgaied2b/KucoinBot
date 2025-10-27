# settings.py — configuration centrale (mise à jour institutionnelle + TP1 dynamique)
import os
from dotenv import load_dotenv

load_dotenv()

ENV = os.getenv("ENV", "production")
TZ = os.getenv("TZ", "Europe/Paris")

# ---------------- Telegram ----------------
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "")

# ---------------- KuCoin ----------------
KUCOIN_API_KEY = os.getenv("KUCOIN_API_KEY", "")
KUCOIN_API_SECRET = os.getenv("KUCOIN_API_SECRET", "")
KUCOIN_API_PASSPHRASE = os.getenv("KUCOIN_API_PASSPHRASE", "")

# ---------------- Base Trading ----------------
MARGIN_USDT = float(os.getenv("MARGIN_USDT", "20"))
LEVERAGE = int(os.getenv("LEVERAGE", "10"))
SCAN_INTERVAL_MIN = int(os.getenv("SCAN_INTERVAL_MIN", "5"))

# ---------------- Guards / Mode ----------------
DRY_RUN = os.getenv("DRY_RUN", "true").lower() in ("1", "true", "yes")
MAX_ORDERS_PER_SCAN = int(os.getenv("MAX_ORDERS_PER_SCAN", "3"))

# ---------------- Institutionnel / Acceptation ----------------
MIN_INST_SCORE = int(os.getenv("MIN_INST_SCORE", "2"))
REQUIRE_STRUCTURE = os.getenv("REQUIRE_STRUCTURE", "true").lower() in ("1", "true", "yes")
REQUIRE_MOMENTUM = os.getenv("REQUIRE_MOMENTUM", "true").lower() in ("1", "true", "yes")
RR_MIN_STRICT = float(os.getenv("RR_MIN_STRICT", "1.6"))
RR_MIN_TOLERATED_WITH_INST = float(os.getenv("RR_MIN_TOLERATED_WITH_INST", "1.3"))

# Garde-fous institutionnels
REQUIRE_HTF_ALIGN = os.getenv("REQUIRE_HTF_ALIGN", "true").lower() in ("1", "true", "yes")
REQUIRE_BOS_QUALITY = os.getenv("REQUIRE_BOS_QUALITY", "true").lower() in ("1", "true", "yes")
COMMITMENT_MIN = float(os.getenv("COMMITMENT_MIN", "0.55"))  # 0..1

# ---------------- Stops ----------------
STOP_TRIGGER_TYPE_SL = os.getenv("STOP_TRIGGER_TYPE_SL", "MP")  # MP=Mark, TP=Last
STOP_TRIGGER_TYPE_TP = os.getenv("STOP_TRIGGER_TYPE_TP", "TP")

ATR_LEN = int(os.getenv("ATR_LEN", "14"))
ATR_MULT_SL = float(os.getenv("ATR_MULT_SL", "2.2"))
STRUCT_LOOKBACK = int(os.getenv("STRUCT_LOOKBACK", "20"))
SL_BUFFER_PCT = float(os.getenv("SL_BUFFER_PCT", "0.0015"))
SL_BUFFER_TICKS = int(os.getenv("SL_BUFFER_TICKS", "2"))

# Clamps SL pro
MAX_SL_PCT = float(os.getenv("MAX_SL_PCT", "0.06"))          # 6% max entre entry et SL
MIN_SL_TICKS = int(os.getenv("MIN_SL_TICKS", "2"))           # min 2 ticks
ATR_MULT_SL_CAP = float(os.getenv("ATR_MULT_SL_CAP", "2.0")) # |entry-SL| ≤ ATR*2

# Liquidité (détection pools)
LIQ_LOOKBACK = int(os.getenv("LIQ_LOOKBACK", "100"))
LIQ_BUFFER_PCT = float(os.getenv("LIQ_BUFFER_PCT", "0.0008"))   # 0.08%
LIQ_BUFFER_TICKS = int(os.getenv("LIQ_BUFFER_TICKS", "3"))
LIQ_TOL_BPS_MIN = int(os.getenv("LIQ_TOL_BPS_MIN", "5"))        # 0.05%
LIQ_TOL_TICKS = int(os.getenv("LIQ_TOL_TICKS", "3"))

# Break-even (buffer frais/slippage)
BE_FEE_BUFFER_TICKS = int(os.getenv("BE_FEE_BUFFER_TICKS", "1"))
BE_DEBOUNCE_MS = int(os.getenv("BE_DEBOUNCE_MS", "400"))

# ---------------- Scope & Features ----------------
TOP_N_SYMBOLS = int(os.getenv("TOP_N_SYMBOLS", "80"))
ENABLE_SQUEEZE_ENGINE = os.getenv("ENABLE_SQUEEZE_ENGINE", "true").lower() in ("1", "true", "yes")
FAIL_OPEN_TO_CORE = os.getenv("FAIL_OPEN_TO_CORE", "true").lower() in ("1", "true", "yes")

# ---------------- Portfolio & Corrélation ----------------
ACCOUNT_EQUITY_USDT = float(os.getenv("ACCOUNT_EQUITY_USDT", "10000"))
MAX_GROSS_EXPOSURE = float(os.getenv("MAX_GROSS_EXPOSURE", "2.0"))      # 200% notionnel max
MAX_SYMBOL_EXPOSURE = float(os.getenv("MAX_SYMBOL_EXPOSURE", "0.25"))   # 25% equity / symbole
CORR_GROUP_CAP = float(os.getenv("CORR_GROUP_CAP", "0.5"))               # 50% equity / groupe corrélé
CORR_BTC_THRESHOLD = float(os.getenv("CORR_BTC_THRESHOLD", "0.7"))
DOM_TREND_STRONG = float(os.getenv("DOM_TREND_STRONG", "0.002"))

# ---------------- Risk / Targets ----------------
RISK_USDT = float(os.getenv("RISK_USDT", "20"))          # Perte max si SL touche
RR_TARGET = float(os.getenv("RR_TARGET", "1.6"))         # RR pour TP par défaut

# TP1 dynamique par volatilité (CLAMP FIXÉ & CORRIGÉ)
TP1_R_CLAMP_MIN = float(os.getenv("TP1_R_CLAMP_MIN", "1.2"))  # corrigé: min ≠ max
TP1_R_CLAMP_MAX = float(os.getenv("TP1_R_CLAMP_MAX", "1.8"))
TP1_R_BY_VOL = os.getenv("TP1_R_BY_VOL", "true").lower() in ("1", "true", "yes")
TP2_R_TARGET = float(os.getenv("TP2_R_TARGET", "2.5"))

TP_HIT_TOL_PCT = float(os.getenv("TP_HIT_TOL_PCT", "0.0005"))      # ±0.05%
TP_HIT_TOL_TICKS_MIN = int(os.getenv("TP_HIT_TOL_TICKS_MIN", "1")) # ≥ 1 tick

# Régimes de volatilité (ATR% / price) pour moduler clamps RR/TP1
VOL_REGIME_ATR_PCT_LOW  = float(os.getenv("VOL_REGIME_ATR_PCT_LOW",  "0.012"))  # 1.2%
VOL_REGIME_ATR_PCT_HIGH = float(os.getenv("VOL_REGIME_ATR_PCT_HIGH", "0.030"))  # 3.0%

# ---------------- Binance (insto) — rate limit & timeouts ----------------
BINANCE_HTTP_TIMEOUT_S = float(os.getenv("BINANCE_HTTP_TIMEOUT_S", "7.0"))
BINANCE_HTTP_RETRIES = int(os.getenv("BINANCE_HTTP_RETRIES", "2"))
BINANCE_MIN_INTERVAL_S = float(os.getenv("BINANCE_MIN_INTERVAL_S", "0.25"))   # ~4 rps/endpoint local

# --------- Derived tuples (ne pas éditer) ----------
TP1_R_CLAMP = (TP1_R_CLAMP_MIN, TP1_R_CLAMP_MAX)
VOL_REGIME_ATR_PCT = (VOL_REGIME_ATR_PCT_LOW, VOL_REGIME_ATR_PCT_HIGH)
