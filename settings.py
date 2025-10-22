import os
from dotenv import load_dotenv

load_dotenv()

ENV = os.getenv("ENV", "development")
TZ = os.getenv("TZ", "Europe/Paris")

# Telegram
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "")

# KuCoin
KUCOIN_API_KEY = os.getenv("KUCOIN_API_KEY", "")
KUCOIN_API_SECRET = os.getenv("KUCOIN_API_SECRET", "")
KUCOIN_API_PASSPHRASE = os.getenv("KUCOIN_API_PASSPHRASE", "")

# Trading
MARGIN_USDT = float(os.getenv("MARGIN_USDT", "20"))
LEVERAGE = int(os.getenv("LEVERAGE", "20"))
SCAN_INTERVAL_MIN = int(os.getenv("SCAN_INTERVAL_MIN", "5"))

# Guards / mode
DRY_RUN = os.getenv("DRY_RUN", "true").lower() in ("1","true","yes")
MAX_ORDERS_PER_SCAN = int(os.getenv("MAX_ORDERS_PER_SCAN", "3"))
MIN_INST_SCORE = int(os.getenv("MIN_INST_SCORE", "2"))
REQUIRE_STRUCTURE = os.getenv("REQUIRE_STRUCTURE", "true").lower() in ("1","true","yes")
REQUIRE_MOMENTUM = os.getenv("REQUIRE_MOMENTUM", "true").lower() in ("1","true","yes")
RR_MIN_STRICT = float(os.getenv("RR_MIN_STRICT","1.6"))
RR_MIN_TOLERATED_WITH_INST = float(os.getenv("RR_MIN_TOLERATED_WITH_INST","1.3"))

# Top-1 tuning
TOP_N_SYMBOLS = int(os.getenv("TOP_N_SYMBOLS","60"))   # scan réduit & qualitatif
MIN_CONFLUENCE_SCORE = int(os.getenv("MIN_CONFLUENCE_SCORE","3"))
SQUEEZE_REQUIRED = os.getenv("SQUEEZE_REQUIRED","false").lower() in ("1","true","yes")

# Type de déclenchement des stops (SL/TP) : "TP" = last trade price, "MP" = mark price
STOP_TRIGGER_TYPE_SL = os.getenv("STOP_TRIGGER_TYPE_SL", "TP")
STOP_TRIGGER_TYPE_TP = os.getenv("STOP_TRIGGER_TYPE_TP", "TP")
