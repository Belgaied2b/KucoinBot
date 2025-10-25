# settings.py — configuration centrale (mise à jour institutionnelle)
import os
from dotenv import load_dotenv

load_dotenv()

ENV = os.getenv("ENV", "production")
TZ = os.getenv("TZ", "Europe/Paris")

# Telegram
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "")

# KuCoin
KUCOIN_API_KEY = os.getenv("KUCOIN_API_KEY", "")
KUCOIN_API_SECRET = os.getenv("KUCOIN_API_SECRET", "")
KUCOIN_API_PASSPHRASE = os.getenv("KUCOIN_API_PASSPHRASE", "")

# Trading de base
MARGIN_USDT = float(os.getenv("MARGIN_USDT", "20"))
LEVERAGE = int(os.getenv("LEVERAGE", "20"))
SCAN_INTERVAL_MIN = int(os.getenv("SCAN_INTERVAL_MIN", "5"))

# Guards / mode
DRY_RUN = os.getenv("DRY_RUN", "true").lower() in ("1", "true", "yes")
MAX_ORDERS_PER_SCAN = int(os.getenv("MAX_ORDERS_PER_SCAN", "3"))

# Institutionnel / Acceptation signal
MIN_INST_SCORE = int(os.getenv("MIN_INST_SCORE", "2"))
REQUIRE_STRUCTURE = os.getenv("REQUIRE_STRUCTURE", "true").lower() in ("1", "true", "yes")
REQUIRE_MOMENTUM = os.getenv("REQUIRE_MOMENTUM", "true").lower() in ("1", "true", "yes")
RR_MIN_STRICT = float(os.getenv("RR_MIN_STRICT", "1.6"))
RR_MIN_TOLERATED_WITH_INST = float(os.getenv("RR_MIN_TOLERATED_WITH_INST", "1.3"))

# 🔥 Nouveaux garde-fous institutionnels
# - Alignement multi-timeframe (H4/D1) requis ?
REQUIRE_HTF_ALIGN = os.getenv("REQUIRE_HTF_ALIGN", "true").lower() in ("1", "true", "yes")
# - Qualité de cassure (BOS) exigeant volume p80 + variation d'OI (± seuils) ?
REQUIRE_BOS_QUALITY = os.getenv("REQUIRE_BOS_QUALITY", "true").lower() in ("1", "true", "yes")
# - Seuil minimum de "commitment" (0..1) basé sur OI + pente CVD
COMMITMENT_MIN = float(os.getenv("COMMITMENT_MIN", "0.55"))

# Stops
STOP_TRIGGER_TYPE_SL = os.getenv("STOP_TRIGGER_TYPE_SL", "MP")  # MP=Mark, TP=Last
STOP_TRIGGER_TYPE_TP = os.getenv("STOP_TRIGGER_TYPE_TP", "TP")

# Scope & features
TOP_N_SYMBOLS = int(os.getenv("TOP_N_SYMBOLS", "60"))
ENABLE_SQUEEZE_ENGINE = os.getenv("ENABLE_SQUEEZE_ENGINE", "true").lower() in ("1", "true", "yes")
FAIL_OPEN_TO_CORE = os.getenv("FAIL_OPEN_TO_CORE", "true").lower() in ("1", "true", "yes")

# ---- Portfolio & Corrélation (nouveau) ----
ACCOUNT_EQUITY_USDT = float(os.getenv("ACCOUNT_EQUITY_USDT", "10000"))
MAX_GROSS_EXPOSURE = float(os.getenv("MAX_GROSS_EXPOSURE", "2.0"))      # 200% notionnel max
MAX_SYMBOL_EXPOSURE = float(os.getenv("MAX_SYMBOL_EXPOSURE", "0.25"))   # 25% equity par symbole
CORR_GROUP_CAP = float(os.getenv("CORR_GROUP_CAP", "0.5"))               # 50% equity par groupe corrélé
CORR_BTC_THRESHOLD = float(os.getenv("CORR_BTC_THRESHOLD", "0.7"))       # corrélation forte à BTC
DOM_TREND_STRONG = float(os.getenv("DOM_TREND_STRONG", "0.002"))         # dominance BTC très forte (≈0.2%/h)

# --- Risk par trade (nouveau) ---
RISK_USDT = float(os.getenv("RISK_USDT", "20"))          # perte max si SL touche
RR_TARGET = float(os.getenv("RR_TARGET", "1.6"))         # RR pour TP par défaut

# --- Stops robustes ---
ATR_LEN = int(os.getenv("ATR_LEN", "14"))
ATR_MULT_SL = float(os.getenv("ATR_MULT_SL", "2.2"))     # SL = max(swing, entry - ATR*mult)
STRUCT_LOOKBACK = int(os.getenv("STRUCT_LOOKBACK", "20"))# swing lookback pour low/high
SL_BUFFER_PCT = float(os.getenv("SL_BUFFER_PCT", "0.0015"))  # 0.15% de buffer
SL_BUFFER_TICKS = int(os.getenv("SL_BUFFER_TICKS", "2"))     # buffer ticks en plus

# --- Break-even (BE) net : buffer ticks pour couvrir frais/slippage ---
# Utilisé par breakeven_manager.py (new_sl = entry ± BE_FEE_BUFFER_TICKS * tick)
BE_FEE_BUFFER_TICKS = int(os.getenv("BE_FEE_BUFFER_TICKS", "1"))
