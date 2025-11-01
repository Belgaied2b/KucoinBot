import json, logging, sys, time, os

class JsonFormatter(logging.Formatter):
    def format(self, record):
        d = {
            "ts": int(time.time()),
            "level": record.levelname,
            "msg": record.getMessage(),
            "logger": record.name,
        }
        if record.exc_info:
            d["exc"] = self.formatException(record.exc_info)
        return json.dumps(d, ensure_ascii=False)

def configure_logging(level: str = None):
    level = level or ("INFO" if os.getenv("ENV","dev").startswith("prod") else "DEBUG")
    root = logging.getLogger()
    root.setLevel(level)
    for h in list(root.handlers):
        root.removeHandler(h)
    h = logging.StreamHandler(sys.stdout)
    h.setFormatter(JsonFormatter())
    root.addHandler(h)
    logging.getLogger("requests").setLevel(logging.WARNING)
    return root
