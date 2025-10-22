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
DRY_RUN = os.getenv("DRY_RUN", "true").lower() in ("1","true","yes")
MAX_ORDERS_PER_SCAN = int(os.getenv("MAX_ORDERS_PER_SCAN", "3"))
MIN_INST_SCORE = int(os.getenv("MIN_INST_SCORE", "2"))
REQUIRE_STRUCTURE = os.getenv("REQUIRE_STRUCTURE", "true").lower() in ("1","true","yes")
REQUIRE_MOMENTUM = os.getenv("REQUIRE_MOMENTUM", "true").lower() in ("1","true","yes")
RR_MIN_STRICT = float(os.getenv("RR_MIN_STRICT", "1.6"))
RR_MIN_TOLERATED_WITH_INST = float(os.getenv("RR_MIN_TOLERATED_WITH_INST", "1.3"))

# Stops
STOP_TRIGGER_TYPE_SL = os.getenv("STOP_TRIGGER_TYPE_SL", "MP")  # MP=Mark, TP=Last
STOP_TRIGGER_TYPE_TP = os.getenv("STOP_TRIGGER_TYPE_TP", "TP")

# Scope & features
TOP_N_SYMBOLS = int(os.getenv("TOP_N_SYMBOLS", "60"))
ENABLE_SQUEEZE_ENGINE = os.getenv("ENABLE_SQUEEZE_ENGINE", "true").lower() in ("1","true","yes")
FAIL_OPEN_TO_CORE = os.getenv("FAIL_OPEN_TO_CORE", "true").lower() in ("1","true","yes")

# ---- Portfolio & Corrélation (nouveau) ----
ACCOUNT_EQUITY_USDT = float(os.getenv("ACCOUNT_EQUITY_USDT", "10000"))
MAX_GROSS_EXPOSURE = float(os.getenv("MAX_GROSS_EXPOSURE", "2.0"))      # 200% notionnel max
MAX_SYMBOL_EXPOSURE = float(os.getenv("MAX_SYMBOL_EXPOSURE", "0.25"))   # 25% equity par symbole
CORR_GROUP_CAP = float(os.getenv("CORR_GROUP_CAP", "0.5"))               # 50% equity par groupe corrélé
CORR_BTC_THRESHOLD = float(os.getenv("CORR_BTC_THRESHOLD", "0.7"))       # corrélation forte à BTC
DOM_TREND_STRONG = float(os.getenv("DOM_TREND_STRONG", "0.002"))         # dominance BTC très forte (≈0.2%/h)
