# logging_setup.py
import logging, os, sys, json, time

LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO").upper()
LOG_JSON  = os.getenv("LOG_JSON", "0").lower() in ("1","true","t","yes","on")
LOG_UTC   = os.getenv("LOG_UTC", "0").lower() in ("1","true","t","yes","on")

class _JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        base = {
            "ts": self.formatTime(record, datefmt="%Y-%m-%dT%H:%M:%S"),
            "level": record.levelname,
            "name": record.name,
            "msg": record.getMessage(),
            "symbol": getattr(record, "symbol", "-"),
            "stage": getattr(record, "stage", "-"),
        }
        if record.exc_info:
            base["exc"] = self.formatException(record.exc_info)
        return json.dumps(base, ensure_ascii=False)

def configure_logging():
    if LOG_UTC:
        logging.Formatter.converter = time.gmtime  # type: ignore
    root = logging.getLogger()
    root.handlers.clear()
    root.setLevel(LOG_LEVEL)
    h = logging.StreamHandler(sys.stdout)
    if LOG_JSON:
        h.setFormatter(_JsonFormatter())
    else:
        h.setFormatter(logging.Formatter("%(asctime)s | %(levelname)-5s | %(name)s | [%(symbol)s] %(message)s"))
    root.addHandler(h)
