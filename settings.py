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

# Trading (ta config existante)
MARGIN_USDT = float(os.getenv("MARGIN_USDT", "20"))
LEVERAGE = int(os.getenv("LEVERAGE", "20"))
SCAN_INTERVAL_MIN = int(os.getenv("SCAN_INTERVAL_MIN", "5"))

# --- Garde-fous institutionnels / risque ---
# Mode simulation : aucune requête d’ordre n’est envoyée (mais tout est logué)
DRY_RUN = os.getenv("DRY_RUN", "true").lower() in ("1", "true", "yes")

# Max d’ordres envoyés à chaque scan (sécurité API & risque)
MAX_ORDERS_PER_SCAN = int(os.getenv("MAX_ORDERS_PER_SCAN", "3"))

# Score institutionnel minimal pour autoriser l’envoi
MIN_INST_SCORE = int(os.getenv("MIN_INST_SCORE", "2"))  # 0..3

# Exigences techniques
REQUIRE_STRUCTURE = os.getenv("REQUIRE_STRUCTURE", "true").lower() in ("1","true","yes")
REQUIRE_MOMENTUM = os.getenv("REQUIRE_MOMENTUM", "true").lower() in ("1","true","yes")

# Seuils RR
RR_MIN_STRICT = float(os.getenv("RR_MIN_STRICT", "1.5"))  # par défaut 1.5
RR_MIN_TOLERATED_WITH_INST = float(os.getenv("RR_MIN_TOLERATED_WITH_INST", "1.2"))  # si inst >= MIN_INST_SCORE
