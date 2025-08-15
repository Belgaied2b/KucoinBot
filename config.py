# config.py — mode institutionnel + full scan
import os
from typing import List
from pydantic import BaseModel, Field

# On n’utilise pas .env. Des variables d’environnement peuvent encore écraser les valeurs par défaut.

def _get_bool(name: str, default: bool) -> bool:
    v = os.getenv(name)
    if v is None:
        return default
    return v.strip().lower() in ("1", "true", "t", "yes", "y", "on")

def _get_float(name: str, default: float) -> float:
    try:
        return float(os.getenv(name, str(default)))
    except Exception:
        return default

def _get_int(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, str(default)))
    except Exception:
        return default

def _get_list(name: str, default: List[str]) -> List[str]:
    raw = os.getenv(name, "")
    if not raw:
        return default
    return [s.strip().upper() for s in raw.split(",") if s.strip()]

class Settings(BaseModel):
    # --- KUCOIN EXECUTION ---
    kucoin_base_url: str   = Field(default_factory=lambda: os.getenv("KUCOIN_BASE_URL", "https://api-futures.kucoin.com"))
    kucoin_ws_url: str     = Field(default_factory=lambda: os.getenv("KUCOIN_WS_URL", "wss://ws-api-futures.kucoin.com/endpoint"))

    # Identifiants intégrés (plus de .env requis)
    kucoin_key: str        = Field(default_factory=lambda: os.getenv("KUCOIN_API_KEY", "6890cfb4dffe710001e6edb0"))
    kucoin_secret: str     = Field(default_factory=lambda: os.getenv("KUCOIN_API_SECRET", "889e4492-c2ff-4c9d-9136-64afe6d5e780"))
    kucoin_passphrase: str = Field(default_factory=lambda: os.getenv("KUCOIN_API_PASSPHRASE", "Nad1703-_"))

    # --- SYMBOLS (full scan institutionnel) ---
    auto_symbols: bool     = Field(default_factory=lambda: _get_bool("AUTO_SYMBOLS", True))   # active l’auto-scan
    symbols: List[str]     = Field(default_factory=lambda: _get_list("SYMBOLS", ["BTCUSDT"])) # ignoré si auto_symbols=True
    symbols_max: int       = Field(default_factory=lambda: _get_int("SYMBOLS_MAX", 500))      # couvre 400+ paires
    exclude_symbols: str   = Field(default_factory=lambda: os.getenv("EXCLUDE_SYMBOLS", ""))  # CSV optionnel

    # --- WARMUP ---
    warmup_seconds: int    = Field(default_factory=lambda: _get_int("WARMUP_SECONDS", 15))

    # --- RISK & ORDERS ---
    # Marge (USDT) par trade — côté trader valueQty = marge * levier
    margin_per_trade: float  = Field(default_factory=lambda: _get_float("MARGIN_PER_TRADE_USDT", 20.0))
    default_leverage: int    = Field(default_factory=lambda: _get_int("DEFAULT_LEVERAGE", 10))

    max_positions: int       = Field(default_factory=lambda: _get_int("MAX_CONCURRENT_POSITIONS", 4))
    sl_atr_mult: float       = Field(default_factory=lambda: _get_float("STOP_BUFFER_ATR_MULT", 1.2))
    tp1_rr: float            = Field(default_factory=lambda: _get_float("TAKE_PROFIT_1_RR", 1.4))
    tp2_rr: float            = Field(default_factory=lambda: _get_float("TAKE_PROFIT_2_RR", 2.2))
    tp1_part: float          = Field(default_factory=lambda: _get_float("TP1_PART", 0.5))
    trail_mult_atr: float    = Field(default_factory=lambda: _get_float("TRAIL_AFTER_TP1_MULT_ATR", 0.8))
    breakeven_after_tp1: bool = Field(default_factory=lambda: _get_bool("MOVE_TO_BE_AFTER_TP1", True))

    # --- SCORING (Institution++) ---
    # Score global = somme pondérée; seuil et composantes stricts
    req_score_min: float     = Field(default_factory=lambda: _get_float("REQ_SCORE_MIN", 2.2))
    req_rr_min: float        = Field(default_factory=lambda: _get_float("REQ_RR_MIN", 1.2))
    allow_tol_rr: bool       = Field(default_factory=lambda: _get_bool("ALLOW_TOLERANCE_RR", True))

    # Pondérations pro (plus d’accent sur OI, Delta, Liq)
    w_oi: float              = Field(default_factory=lambda: _get_float("W_OI", 0.35))
    w_funding: float         = Field(default_factory=lambda: _get_float("W_FUNDING", 0.15))
    w_delta: float           = Field(default_factory=lambda: _get_float("W_DELTA", 0.25))
    w_liq: float             = Field(default_factory=lambda: _get_float("W_LIQ", 0.20))
    w_book_imbal: float      = Field(default_factory=lambda: _get_float("W_BOOK_IMBAL", 0.05))

    # Seuils “gates” par composant (stricts)
    inst_components_min: int = Field(default_factory=lambda: _get_int("INST_COMPONENTS_MIN", 2))
    oi_req_min: float        = Field(default_factory=lambda: _get_float("OI_REQ_MIN", 0.40))
    delta_req_min: float     = Field(default_factory=lambda: _get_float("DELTA_REQ_MIN", 0.40))
    funding_req_min: float   = Field(default_factory=lambda: _get_float("FUNDING_REQ_MIN", 0.20))
    liq_req_min: float       = Field(default_factory=lambda: _get_float("LIQ_REQ_MIN", 0.50))
    book_req_min: float      = Field(default_factory=lambda: _get_float("BOOK_REQ_MIN", 0.30))
    use_book_imbal: bool     = Field(default_factory=lambda: _get_bool("USE_BOOK_IMBAL", False))

    # Normalisation des enrichissements Binance
    funding_ref: float       = Field(default_factory=lambda: _get_float("FUNDING_REF", 0.00025))  # 0.025% -> 1.0
    oi_delta_ref: float      = Field(default_factory=lambda: _get_float("OI_DELTA_REF", 0.02))    # ΔOI 2% (5m) -> 1.0
    oi_fund_refresh_sec: int = Field(default_factory=lambda: _get_int("OI_FUND_REFRESH_SEC", 45))

    # --- LIQ PACK ---
    use_legacy_binance_liq: bool = Field(default_factory=lambda: _get_bool("USE_LEGACY_BINANCE_LIQ", False))
    liq_refresh_sec: int         = Field(default_factory=lambda: _get_int("LIQ_REFRESH_SEC", 30))
    liq_notional_norm: float     = Field(default_factory=lambda: _get_float("LIQ_NOTIONAL_NORM", 150_000.0))
    liq_imbal_weight: float      = Field(default_factory=lambda: _get_float("LIQ_IMBAL_WEIGHT", 0.35))
    liq_notional_overrides: str  = Field(default_factory=lambda: os.getenv("LIQ_NOTIONAL_OVERRIDES", "{}"))

    # --- PERSISTENCE / COOLDOWN ---
    persist_win: int             = Field(default_factory=lambda: _get_int("PERSIST_WIN", 3))  # 2/3 fenêtres
    persist_min_ok: int          = Field(default_factory=lambda: _get_int("PERSIST_MIN_OK", 2))
    symbol_cooldown_sec: int     = Field(default_factory=lambda: _get_int("SYMBOL_COOLDOWN_SEC", 900))
    min_liq_norm: float          = Field(default_factory=lambda: _get_float("MIN_LIQ_NORM", 0.0))

    # --- MACRO ---
    use_macro: bool              = Field(default_factory=lambda: _get_bool("USE_MACRO", True))
    use_total2: bool             = Field(default_factory=lambda: _get_bool("USE_TOTAL2", True))
    macro_refresh_minutes: int   = Field(default_factory=lambda: _get_int("MACRO_REFRESH_MINUTES", 5))

    # --- EXECUTION TACTICS ---
    post_only_entries: bool      = Field(default_factory=lambda: _get_bool("POST_ONLY_ENTRIES", True))
    entry_timeout_sec: float     = Field(default_factory=lambda: _get_float("ENTRY_TIMEOUT_SEC", 3.0))
    max_requotes: int            = Field(default_factory=lambda: _get_int("MAX_REQUOTES", 2))
    max_maker_slippage_ticks: int = Field(default_factory=lambda: _get_int("MAX_MAKER_SLIPPAGE_TICKS", 5))
    adverse_sweep_threshold: float = Field(default_factory=lambda: _get_float("ADVERSE_SWEEP_THRESHOLD", 0.35))
    cancel_on_adverse: bool      = Field(default_factory=lambda: _get_bool("CANCEL_ON_ADVERSE", True))
    two_stage_entry: bool        = Field(default_factory=lambda: _get_bool("TWO_STAGE_ENTRY", True))
    stage1_fraction: float       = Field(default_factory=lambda: _get_float("STAGE1_FRACTION", 0.35))

    # --- EXECUTION (V1.1-bis) ---
    use_ioc_fallback: bool       = Field(default_factory=lambda: _get_bool("USE_IOC_FALLBACK", True))
    default_tick_size: float     = Field(default_factory=lambda: _get_float("DEFAULT_TICK_SIZE", 0.1))

    # --- DIVERS / HTTP ---
    http_timeout_sec: float      = Field(default_factory=lambda: _get_float("HTTP_TIMEOUT_SEC", 6.0))

    # --- ENV / LOGS ---
    env: str                     = Field(default_factory=lambda: os.getenv("ENV", "prod"))
    log_signals: bool            = Field(default_factory=lambda: _get_bool("LOG_SIGNALS", True))
    persist_path: str            = Field(default_factory=lambda: os.getenv("PERSIST_PATH", "./runtime_state.json"))

    # --- TELEGRAM ---
    tg_token: str                = Field(default_factory=lambda: os.getenv("TELEGRAM_BOT_TOKEN", "7605602027:AAFTBVopeZQYBh8ZtoudgU5oykuWbZtDz2o"))
    tg_chat: str                 = Field(default_factory=lambda: os.getenv("TELEGRAM_CHAT_ID", "5485398553"))

SETTINGS = Settings()
