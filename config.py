import os

# Universe
AUTO_UNIVERSE = os.getenv("AUTO_UNIVERSE","1").lower() in ("1","true","t","yes","on")
UNIVERSE_LIMIT = int(os.getenv("UNIVERSE_LIMIT","450"))
SYMBOLS = [s.strip().upper() for s in os.getenv("SYMBOLS","").split(",") if s.strip()]

# Event loop
WS_FORCE_ALWAYS = os.getenv("WS_FORCE_ALWAYS","1").lower() in ("1","true","t","yes","on")
WS_POLL_SEC = float(os.getenv("WS_POLL_SEC","5"))  # polling cadence

# Execution
ORDER_VALUE_USDT = float(os.getenv("ORDER_VALUE_USDT","20"))
POST_ONLY = os.getenv("KC_POST_ONLY","1").lower() in ("1","true","t","yes","on")
LEVERAGE = int(os.getenv("LEVERAGE","5"))
SLIPPAGE_BPS = float(os.getenv("SLIPPAGE_BPS","3.0"))

# Risk / Scaling
MAX_CONCURRENCY = int(os.getenv("MAX_CONCURRENCY","16"))
GLOBAL_RISK_BUDGET_USDT = float(os.getenv("GLOBAL_RISK_BUDGET_USDT","1000"))

# HTTP
HTTP_TIMEOUT = float(os.getenv("HTTP_TIMEOUT","10"))
RETRY = int(os.getenv("HTTP_RETRY","2"))

# KuCoin
KUCOIN_BASE_URL = os.getenv("KUCOIN_BASE_URL","https://api-futures.kucoin.com")
KUCOIN_KEY = os.getenv("KUCOIN_KEY","")
KUCOIN_SECRET = os.getenv("KUCOIN_SECRET","")
KUCOIN_PASSPHRASE = os.getenv("KUCOIN_PASSPHRASE","")
DRY_RUN = os.getenv("DRY_RUN","0").lower() in ("1","true","t","yes","on")

# Caching/Scheduling
H1_TTL_SEC = int(os.getenv("H1_TTL_SEC","240"))
H4_TTL_SEC = int(os.getenv("H4_TTL_SEC","1200"))
ANALYZE_COOLDOWN_SEC = int(os.getenv("ANALYZE_COOLDOWN_SEC","300"))
JITTER_SEC = int(os.getenv("JITTER_SEC","60"))

# WS Venues (enable/disable)
WS_ENABLE_BINANCE = os.getenv("WS_ENABLE_BINANCE","1").lower() in ("1","true","t","yes","on")
WS_ENABLE_OKX     = os.getenv("WS_ENABLE_OKX","1").lower() in ("1","true","t","yes","on")
WS_ENABLE_BYBIT   = os.getenv("WS_ENABLE_BYBIT","1").lower() in ("1","true","t","yes","on")

# SOR venues enabled for execution (for now we only ship KuCoin execution implemented)
SOR_ENABLE_KUCOIN = os.getenv("SOR_ENABLE_KUCOIN","1").lower() in ("1","true","t","yes","on")
SOR_ENABLE_BINANCE= os.getenv("SOR_ENABLE_BINANCE","0").lower() in ("1","true","t","yes","on")  # stub
SOR_ENABLE_OKX    = os.getenv("SOR_ENABLE_OKX","0").lower() in ("1","true","t","yes","on")      # stub
SOR_ENABLE_BYBIT  = os.getenv("SOR_ENABLE_BYBIT","0").lower() in ("1","true","t","yes","on")    # stub
