import os
import json
from typing import Dict, List
from pydantic import BaseModel, Field, validator
from dotenv import load_dotenv

load_dotenv()

# --- Aliases Railway (si ton env utilise TOKEN / CHAT_ID) ---
os.environ.setdefault("TELEGRAM_BOT_TOKEN", os.getenv("TOKEN", ""))
os.environ.setdefault("TELEGRAM_CHAT_ID",   os.getenv("CHAT_ID", ""))


class Settings(BaseModel):
    # =============== KUCOIN EXECUTION ===============
    kucoin_base_url: str  = Field(default_factory=lambda: os.getenv("KUCOIN_BASE_URL", "https://api-futures.kucoin.com"))
    kucoin_key: str       = Field(default_factory=lambda: os.getenv("KUCOIN_API_KEY", ""))
    kucoin_secret: str    = Field(default_factory=lambda: os.getenv("KUCOIN_API_SECRET", ""))
    kucoin_passphrase: str= Field(default_factory=lambda: os.getenv("KUCOIN_API_PASSPHRASE", ""))

    # =============== SYMBOLS / DISCOVERY ===============
    symbols: List[str]    = Field(default_factory=lambda: [s.strip().upper() for s in os.getenv("SYMBOLS", "BTCUSDT,ETHUSDT,SOLUSDT").split(",") if s.strip()])
    auto_symbols: bool    = Field(default_factory=lambda: os.getenv("AUTO_SYMBOLS", "1").lower() in ("1","true","yes"))
    symbols_max: int      = Field(default_factory=lambda: int(os.getenv("SYMBOLS_MAX", "40")))
    exclude_symbols: str  = Field(default_factory=lambda: os.getenv("EXCLUDE_SYMBOLS", ""))

    # =============== STARTUP ===============
    warmup_seconds: int   = Field(default_factory=lambda: int(os.getenv("WARMUP_SECONDS", "15")))

    # =============== RISK & ORDERS ===============
    margin_per_trade: float = Field(default_factory=lambda: float(os.getenv("MARGIN_PER_TRADE_USDT", "20")))
    max_positions: int      = Field(default_factory=lambda: int(os.getenv("MAX_CONCURRENT_POSITIONS", "4")))
    sl_atr_mult: float      = Field(default_factory=lambda: float(os.getenv("STOP_BUFFER_ATR_MULT", "1.2")))
    tp1_rr: float           = Field(default_factory=lambda: float(os.getenv("TAKE_PROFIT_1_RR", "1.4")))
    tp2_rr: float           = Field(default_factory=lambda: float(os.getenv("TAKE_PROFIT_2_RR", "2.2")))
    tp1_part: float         = Field(default_factory=lambda: float(os.getenv("TP1_PART", "0.5")))
    trail_mult_atr: float   = Field(default_factory=lambda: float(os.getenv("TRAIL_AFTER_TP1_MULT_ATR", "0.8")))
    breakeven_after_tp1: bool = Field(default_factory=lambda: os.getenv("MOVE_TO_BE_AFTER_TP1", "1").lower() in ("1","true","yes"))

    # Cooldown & persistance (gate)
    symbol_cooldown_sec: int = Field(default_factory=lambda: int(os.getenv("SYMBOL_COOLDOWN_SEC", "900")))
    persist_win: int         = Field(default_factory=lambda: int(os.getenv("PERSIST_WIN", "3")))
    persist_min_ok: int      = Field(default_factory=lambda: int(os.getenv("PERSIST_MIN_OK", "2")))

    # =============== SCORING (Institution++) ===============
    # Global score = somme pondérée des sous-scores
    req_score_min: float     = Field(default_factory=lambda: float(os.getenv("REQ_SCORE_MIN", "2.2")))
    req_rr_min: float        = Field(default_factory=lambda: float(os.getenv("REQ_RR_MIN", "1.2")))
    allow_tol_rr: bool       = Field(default_factory=lambda: os.getenv("ALLOW_TOLERANCE_RR", "1").lower() in ("1","true","yes"))

    # Pondérations (lisibles depuis l'env)
    w_oi: float           = Field(default_factory=lambda: float(os.getenv("W_OI", "0.35")))
    w_funding: float      = Field(default_factory=lambda: float(os.getenv("W_FUNDING", "0.15")))
    w_delta: float        = Field(default_factory=lambda: float(os.getenv("W_DELTA", "0.25")))
    w_liq: float          = Field(default_factory=lambda: float(os.getenv("W_LIQ", "0.10")))
    w_book_imbal: float   = Field(default_factory=lambda: float(os.getenv("W_BOOK_IMBAL", "0.15")))

    # Seuils composants (gate)
    inst_components_min: int = Field(default_factory=lambda: int(os.getenv("INST_COMPONENTS_MIN", "2")))
    oi_req_min: float        = Field(default_factory=lambda: float(os.getenv("OI_REQ_MIN", "0.40")))
    delta_req_min: float     = Field(default_factory=lambda: float(os.getenv("DELTA_REQ_MIN", "0.40")))
    funding_req_min: float   = Field(default_factory=lambda: float(os.getenv("FUNDING_REQ_MIN", "0.20")))
    liq_req_min: float       = Field(default_factory=lambda: float(os.getenv("LIQ_REQ_MIN", "0.50")))
    book_req_min: float      = Field(default_factory=lambda: float(os.getenv("BOOK_REQ_MIN", "0.30")))
    use_book_imbal: bool     = Field(default_factory=lambda: os.getenv("USE_BOOK_IMBAL", "0").lower() in ("1","true","yes"))

    # Références de normalisation pour OI & Funding (enrichissement Binance)
    funding_ref: float       = Field(default_factory=lambda: float(os.getenv("FUNDING_REF", "0.00025")))  # 0.025% -> score 1.0
    oi_delta_ref: float      = Field(default_factory=lambda: float(os.getenv("OI_DELTA_REF", "0.02")))     # 2% ΔOI -> score 1.0

    # Refresh externes
    liq_refresh_sec: int     = Field(default_factory=lambda: int(os.getenv("LIQ_REFRESH_SEC", "30")))
    oi_fund_refresh_sec: int = Field(default_factory=lambda: int(os.getenv("OI_FUND_REFRESH_SEC", "45")))
    http_timeout_sec: float  = Field(default_factory=lambda: float(os.getenv("HTTP_TIMEOUT_SEC", "6.0")))

    # Filtre d’activité min (si tu utilises le liq_pack)
    min_liq_norm: float      = Field(default_factory=lambda: float(os.getenv("MIN_LIQ_NORM", "0.0")))

    # =============== LIQ PACK (normalisation) ===============
    liq_notional_norm: float = Field(default_factory=lambda: float(os.getenv("LIQ_NOTIONAL_NORM", "150000")))  # défaut global
    liq_imbal_weight: float  = Field(default_factory=lambda: float(os.getenv("LIQ_IMBAL_WEIGHT", "0.35")))     # poids imbalance [0..1]
    # overrides par symbole au format JSON: {"BTCUSDT": 3000000, "ETHUSDT": 1200000}
    liq_notional_overrides: Dict[str, float] = Field(
        default_factory=lambda: (
            json.loads(os.getenv("LIQ_NOTIONAL_OVERRIDES", "{}"))
            if os.getenv("LIQ_NOTIONAL_OVERRIDES") else {}
        )
    )

    # =============== MACRO ===============
    use_macro: bool           = Field(default_factory=lambda: os.getenv("USE_MACRO", "1").lower() in ("1","true","yes"))
    use_total2: bool          = Field(default_factory=lambda: os.getenv("USE_TOTAL2", "1").lower() in ("1","true","yes"))
    macro_refresh_minutes: int= Field(default_factory=lambda: int(os.getenv("MACRO_REFRESH_MINUTES", "5")))

    # =============== EXECUTION TACTICS (V1.1) ===============
    post_only_entries: bool   = Field(default_factory=lambda: os.getenv("POST_ONLY_ENTRIES", "1").lower() in ("1","true","yes"))
    entry_timeout_sec: float  = Field(default_factory=lambda: float(os.getenv("ENTRY_TIMEOUT_SEC", "3.0")))
    max_requotes: int         = Field(default_factory=lambda: int(os.getenv("MAX_REQUOTES", "2")))
    max_maker_slippage_ticks:int = Field(default_factory=lambda: int(os.getenv("MAX_MAKER_SLIPPAGE_TICKS", "5")))
    adverse_sweep_threshold: float = Field(default_factory=lambda: float(os.getenv("ADVERSE_SWEEP_THRESHOLD", "0.35")))
    cancel_on_adverse: bool   = Field(default_factory=lambda: os.getenv("CANCEL_ON_ADVERSE", "1").lower() in ("1","true","yes"))
    two_stage_entry: bool     = Field(default_factory=lambda: os.getenv("TWO_STAGE_ENTRY", "1").lower() in ("1","true","yes"))
    stage1_fraction: float    = Field(default_factory=lambda: float(os.getenv("STAGE1_FRACTION", "0.35")))

    # =============== EXECUTION (V1.1-bis) ===============
    use_ioc_fallback: bool    = Field(default_factory=lambda: os.getenv("USE_IOC_FALLBACK", "1").lower() in ("1","true","yes"))
    default_tick_size: float  = Field(default_factory=lambda: float(os.getenv("DEFAULT_TICK_SIZE", "0.1")))

    # =============== ENV / LOGS ===============
    env: str                  = Field(default_factory=lambda: os.getenv("ENV", "prod"))
    log_signals: bool         = Field(default_factory=lambda: os.getenv("LOG_SIGNALS", "1").lower() in ("1","true","yes"))
    persist_path: str         = Field(default_factory=lambda: os.getenv("PERSIST_PATH", "./runtime_state.json"))

    # =============== TELEGRAM ===============
    tg_token: str             = Field(default_factory=lambda: os.getenv("TELEGRAM_BOT_TOKEN", ""))
    tg_chat: str              = Field(default_factory=lambda: os.getenv("TELEGRAM_CHAT_ID", ""))

    # --- petits garde-fous ---
    @validator("tp1_part")
    def _tp1_part_range(cls, v):
        return min(1.0, max(0.0, v))

    @validator("stage1_fraction")
    def _stage_fraction_range(cls, v):
        return min(1.0, max(0.0, v))

    @validator("w_oi","w_funding","w_delta","w_liq","w_book_imbal", pre=True)
    def _weights_non_negative(cls, v):
        # autorise 0, mais pas négatif
        v = float(v)
        if v < 0:
            return 0.0
        return v


SETTINGS = Settings()
