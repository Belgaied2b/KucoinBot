import os
from pydantic import BaseModel, Field
from dotenv import load_dotenv
load_dotenv()

class Settings(BaseModel):
    # --- KUCOIN EXECUTION ---
    kucoin_base_url: str = Field(default_factory=lambda: os.getenv("KUCOIN_BASE_URL","https://api-futures.kucoin.com"))
    kucoin_key: str = Field(default_factory=lambda: os.getenv("KUCOIN_API_KEY",""))
    kucoin_secret: str = Field(default_factory=lambda: os.getenv("KUCOIN_API_SECRET",""))
    kucoin_passphrase: str = Field(default_factory=lambda: os.getenv("KUCOIN_API_PASSPHRASE",""))

    # --- SYMBOLS ---
    symbols: list[str] = Field(default_factory=lambda: [s.strip().upper() for s in os.getenv("SYMBOLS","BTCUSDT,ETHUSDT,SOLUSDT").split(",") if s.strip()])

    # --- RISK & ORDERS ---
    margin_per_trade: float = Field(default_factory=lambda: float(os.getenv("MARGIN_PER_TRADE_USDT","20")))
    max_positions: int = Field(default_factory=lambda: int(os.getenv("MAX_CONCURRENT_POSITIONS","4")))
    sl_atr_mult: float = Field(default_factory=lambda: float(os.getenv("STOP_BUFFER_ATR_MULT","1.2")))
    tp1_rr: float = Field(default_factory=lambda: float(os.getenv("TAKE_PROFIT_1_RR","1.4")))
    tp2_rr: float = Field(default_factory=lambda: float(os.getenv("TAKE_PROFIT_2_RR","2.2")))
    tp1_part: float = Field(default_factory=lambda: float(os.getenv("TP1_PART","0.5")))
    trail_mult_atr: float = Field(default_factory=lambda: float(os.getenv("TRAIL_AFTER_TP1_MULT_ATR","0.8")))
    breakeven_after_tp1: bool = Field(default_factory=lambda: os.getenv("MOVE_TO_BE_AFTER_TP1","1")=="1")

    # --- SCORING (Institution++) ---
    req_score_min: float = Field(default_factory=lambda: float(os.getenv("REQ_SCORE_MIN","2.2")))
    req_rr_min: float = Field(default_factory=lambda: float(os.getenv("REQ_RR_MIN","1.2")))
    allow_tol_rr: bool   = Field(default_factory=lambda: os.getenv("ALLOW_TOLERANCE_RR","1")=="1")

    # Pondérations flux
    w_oi: float = 0.35
    w_funding: float = 0.15
    w_delta: float = 0.25
    w_liq: float = 0.10
    w_book_imbal: float = 0.15

    # --- MACRO ---
    use_macro: bool = Field(default_factory=lambda: os.getenv("USE_MACRO","1")=="1")
    use_total2: bool = Field(default_factory=lambda: os.getenv("USE_TOTAL2","1")=="1")
    macro_refresh_minutes: int = Field(default_factory=lambda: int(os.getenv("MACRO_REFRESH_MINUTES","5")))

    # --- EXECUTION TACTICS (V1.1) ---
    post_only_entries: bool = Field(default_factory=lambda: os.getenv("POST_ONLY_ENTRIES","1")=="1")
    entry_timeout_sec: float = Field(default_factory=lambda: float(os.getenv("ENTRY_TIMEOUT_SEC","3.0")))
    max_requotes: int = Field(default_factory=lambda: int(os.getenv("MAX_REQUOTES","2")))
    max_maker_slippage_ticks: int = Field(default_factory=lambda: int(os.getenv("MAX_MAKER_SLIPPAGE_TICKS","5")))
    adverse_sweep_threshold: float = Field(default_factory=lambda: float(os.getenv("ADVERSE_SWEEP_THRESHOLD","0.35")))
    cancel_on_adverse: bool = Field(default_factory=lambda: os.getenv("CANCEL_ON_ADVERSE","1")=="1")
    two_stage_entry: bool = Field(default_factory=lambda: os.getenv("TWO_STAGE_ENTRY","1")=="1")
    stage1_fraction: float = Field(default_factory=lambda: float(os.getenv("STAGE1_FRACTION","0.35")))

    # --- EXECUTION (V1.1-bis) ---
    use_ioc_fallback: bool = Field(default_factory=lambda: os.getenv("USE_IOC_FALLBACK","1")=="1")
    default_tick_size: float = Field(default_factory=lambda: float(os.getenv("DEFAULT_TICK_SIZE","0.1")))

    # --- ENV / LOGS ---
    env: str = Field(default_factory=lambda: os.getenv("ENV","prod"))
    log_signals: bool = True
    persist_path: str = Field(default_factory=lambda: os.getenv("PERSIST_PATH","./runtime_state.json"))

    # --- TELEGRAM ---
    tg_token: str = Field(default_factory=lambda: os.getenv("TELEGRAM_BOT_TOKEN",""))
    tg_chat: str = Field(default_factory=lambda: os.getenv("TELEGRAM_CHAT_ID",""))

SETTINGS = Settings()
