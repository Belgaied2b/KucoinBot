# settings.py — configuration centrale (mise à jour institutionnelle & TP/SL)
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

# 🔥 Garde-fous institutionnels
REQUIRE_HTF_ALIGN = os.getenv("REQUIRE_HTF_ALIGN", "true").lower() in ("1", "true", "yes")
REQUIRE_BOS_QUALITY = os.getenv("REQUIRE_BOS_QUALITY", "true").lower() in ("1", "true", "yes")
COMMITMENT_MIN = float(os.getenv("COMMITMENT_MIN", "0.55"))  # 0..1

# Stops: types de trigger
STOP_TRIGGER_TYPE_SL = os.getenv("STOP_TRIGGER_TYPE_SL", "MP")  # MP=Mark, TP=Last (noms selon ton wrapper)
STOP_TRIGGER_TYPE_TP = os.getenv("STOP_TRIGGER_TYPE_TP", "TP")

# Scope & features
TOP_N_SYMBOLS = int(os.getenv("TOP_N_SYMBOLS", "60"))
ENABLE_SQUEEZE_ENGINE = os.getenv("ENABLE_SQUEEZE_ENGINE", "true").lower() in ("1", "true", "yes")
FAIL_OPEN_TO_CORE = os.getenv("FAIL_OPEN_TO_CORE", "true").lower() in ("1", "true", "yes")

# ---- Portfolio & Corrélation ----
ACCOUNT_EQUITY_USDT = float(os.getenv("ACCOUNT_EQUITY_USDT", "10000"))
MAX_GROSS_EXPOSURE = float(os.getenv("MAX_GROSS_EXPOSURE", "2.0"))      # 200% notionnel max
MAX_SYMBOL_EXPOSURE = float(os.getenv("MAX_SYMBOL_EXPOSURE", "0.25"))   # 25% equity par symbole
CORR_GROUP_CAP = float(os.getenv("CORR_GROUP_CAP", "0.5"))               # 50% equity par groupe corrélé
CORR_BTC_THRESHOLD = float(os.getenv("CORR_BTC_THRESHOLD", "0.7"))       # corrélation forte à BTC
DOM_TREND_STRONG = float(os.getenv("DOM_TREND_STRONG", "0.002"))         # dominance BTC très forte (≈0.2%/h)

# --- Risk par trade ---
RISK_USDT = float(os.getenv("RISK_USDT", "20"))          # perte max si SL touche
RR_TARGET = float(os.getenv("RR_TARGET", "1.6"))         # RR pour TP par défaut

# --- Stops robustes (structure + ATR + buffers) ---
ATR_LEN = int(os.getenv("ATR_LEN", "14"))
ATR_MULT_SL = float(os.getenv("ATR_MULT_SL", "2.2"))          # SL = min(swing, entry ± ATR*mult) côté long/short
STRUCT_LOOKBACK = int(os.getenv("STRUCT_LOOKBACK", "20"))     # swing lookback pour low/high
SL_BUFFER_PCT = float(os.getenv("SL_BUFFER_PCT", "0.0015"))   # 0.15% de buffer
SL_BUFFER_TICKS = int(os.getenv("SL_BUFFER_TICKS", "2"))      # buffer ticks en plus

# --- Clamps SL additionnels (utilisés par stops.py mis à jour) ---
MAX_SL_PCT = float(os.getenv("MAX_SL_PCT", "0.06"))      # distance SL max = 6% de l'entry
MIN_SL_TICKS = int(os.getenv("MIN_SL_TICKS", "2"))       # distance minimale (>= 2 ticks)
ATR_MULT_SL_CAP = float(os.getenv("ATR_MULT_SL_CAP", "2.0"))  # cap absolu = ATR * 2.0

# --- Cibles TP cohérentes (utilisées par exits_manager.py) ---
# TP1_R_CLAMP peut être fixe (min=max) ou dynamique si TP1_R_BY_VOL=True
TP1_R_CLAMP_MIN = float(os.getenv("TP1_R_CLAMP_MIN", "1.5"))
TP1_R_CLAMP_MAX = float(os.getenv("TP1_R_CLAMP_MAX", "1.5"))
TP1_R_CLAMP = (TP1_R_CLAMP_MIN, TP1_R_CLAMP_MAX)         # tuple consommé par le code
TP2_R_TARGET = float(os.getenv("TP2_R_TARGET", "2.5"))
MIN_TP_TICKS = int(os.getenv("MIN_TP_TICKS", "1"))       # écart mini entry→TP1 et TP1→TP2

# Mode TP1 dynamique par régime de volatilité (optionnel)
TP1_R_BY_VOL = os.getenv("TP1_R_BY_VOL", "false").lower() in ("1", "true", "yes")
# bornes de régime ATR% (ATR/Close)
VOL_REGIME_ATR_PCT_LOW = float(os.getenv("VOL_REGIME_ATR_PCT_LOW", "0.015"))   # 1.5%
VOL_REGIME_ATR_PCT_HIGH = float(os.getenv("VOL_REGIME_ATR_PCT_HIGH", "0.035")) # 3.5%
VOL_REGIME_ATR_PCT = (VOL_REGIME_ATR_PCT_LOW, VOL_REGIME_ATR_PCT_HIGH)

# --- Break-even (BE) net : buffer ticks pour couvrir frais/slippage ---
BE_FEE_BUFFER_TICKS = int(os.getenv("BE_FEE_BUFFER_TICKS", "1"))

# (Optionnel) BE: tolérance de hit TP1 & debounce (si ton breakeven_manager les exploite)
TP_HIT_TOL_PCT = float(os.getenv("TP_HIT_TOL_PCT", "0.0005"))      # ±0.05%
TP_HIT_TOL_TICKS_MIN = int(os.getenv("TP_HIT_TOL_TICKS_MIN", "1")) # ≥ 1 tick
BE_DEBOUNCE_MS = int(os.getenv("BE_DEBOUNCE_MS", "400"))

# --- Ratelimit réseau Binance (institutional_data) — facultatif ---
BINANCE_MIN_INTERVAL_S = float(os.getenv("BINANCE_MIN_INTERVAL_S", "0.25"))   # ~4 rps/endpoint local
BINANCE_HTTP_TIMEOUT_S = float(os.getenv("BINANCE_HTTP_TIMEOUT_S", "7.0"))
BINANCE_HTTP_RETRIES = int(os.getenv("BINANCE_HTTP_RETRIES", "2"))
BINANCE_SYMBOLS_TTL_S = int(os.getenv("BINANCE_SYMBOLS_TTL_S", "900"))        # 15 min

# --- Divers exec/retry post-only KuCoin (si tu l’implémentes côté scanner) ---
RETRY_300011_MAX = int(os.getenv("RETRY_300011_MAX", "4"))
RETRY_BACKOFF_MS_BASE = int(os.getenv("RETRY_BACKOFF_MS_BASE", "250"))
RETRY_BACKOFF_JITTER_MIN = int(os.getenv("RETRY_BACKOFF_JITTER_MIN", "50"))
RETRY_BACKOFF_JITTER_MAX = int(os.getenv("RETRY_BACKOFF_JITTER_MAX", "200"))
PRICE_NUDGE_TICKS_MIN = int(os.getenv("PRICE_NUDGE_TICKS_MIN", "0"))
PRICE_NUDGE_TICKS_MAX = int(os.getenv("PRICE_NUDGE_TICKS_MAX", "2"))
SLIPPAGE_TICKS_LIMIT = int(os.getenv("SLIPPAGE_TICKS_LIMIT", "2"))
