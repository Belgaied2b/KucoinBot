# log_setup.py
import logging, os

class SymFormatter(logging.Formatter):
    def format(self, record):
        sym = getattr(record, "symbol", None)
        record.sym = f"[{sym}] " if sym else ""
        return super().format(record)

class SymbolAllowFilter(logging.Filter):
    def __init__(self, allow_csv: str | None):
        super().__init__()
        allow = (allow_csv or "").strip()
        self.allow = {s.strip().upper() for s in allow.split(",") if s.strip()} if allow else set()
    def filter(self, record: logging.LogRecord) -> bool:
        if not self.allow:
            return True
        sym = getattr(record, "symbol", None)
        return (str(sym).upper() in self.allow) if sym else True

def init_logging():
    level = os.getenv("LOG_LEVEL", "INFO").upper()
    root = logging.getLogger()
    root.handlers.clear()
    root.setLevel(level)
    fmt = "%(asctime)s [%(levelname)s] %(sym)s%(name)s: %(message)s"
    handler = logging.StreamHandler()
    handler.setFormatter(SymFormatter(fmt))
    handler.addFilter(SymbolAllowFilter(os.getenv("LOG_SYMBOLS")))  # ex: LOG_SYMBOLS="BTCUSDTM,ETHUSDTM"
    root.addHandler(handler)

def enable_httpx(enable: bool = True):
    lvl = logging.INFO if enable else logging.WARNING
    for n in ("httpx", "httpcore"):
        logging.getLogger(n).setLevel(lvl)
