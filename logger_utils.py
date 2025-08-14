# logger_utils.py
import logging
import os
import sys
import json
from typing import Optional

LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO").upper()
LOG_JSON  = os.getenv("LOG_JSON", "0") in ("1", "true", "True")

class SymbolFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        if not hasattr(record, "symbol"):
            record.symbol = "-"
        return True

class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        base = {
            "ts": self.formatTime(record, datefmt="%Y-%m-%dT%H:%M:%S"),
            "level": record.levelname,
            "name": record.name,
            "symbol": getattr(record, "symbol", "-"),
            "msg": record.getMessage(),
        }
        if record.exc_info:
            base["exc_info"] = self.formatException(record.exc_info)
        return json.dumps(base, ensure_ascii=False)

def get_logger(name: str, symbol: Optional[str] = None) -> logging.Logger:
    logger = logging.getLogger(name if not symbol else f"{name}.{symbol}")
    if logger.handlers:
        return logger
    logger.setLevel(getattr(logging, LOG_LEVEL, logging.INFO))
    handler = logging.StreamHandler(sys.stdout)
    if LOG_JSON:
        fmt = JsonFormatter()
    else:
        fmt = logging.Formatter(
            fmt="%(asctime)s | %(levelname)-5s | %(name)s | [%(symbol)s] %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S",
        )
    handler.setFormatter(fmt)
    handler.addFilter(SymbolFilter())
    logger.addHandler(handler)
    logger.propagate = False
    return logger
