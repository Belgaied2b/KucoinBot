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

# Flags
IS_PROD = ENV.lower().startswith("prod")
