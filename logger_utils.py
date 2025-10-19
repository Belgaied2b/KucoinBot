# logger_utils.py
import logging
import os
import sys
import json
import time
from typing import Optional


def _get_bool(env: str, default: bool = False) -> bool:
    v = os.getenv(env)
    if v is None:
        return default
    return v.strip().lower() in ("1", "true", "t", "yes", "y", "on")


LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO").upper()
LOG_JSON  = _get_bool("LOG_JSON", False)
LOG_UTC   = _get_bool("LOG_UTC", False)  # si True, timestamps en UTC
LOG_FILE  = os.getenv("LOG_FILE", "").strip()  # chemin fichier (optionnel)
DATEFMT   = "%Y-%m-%d %H:%M:%S"


class SymbolFilter(logging.Filter):
    """Ajoute toujours l'attribut 'symbol' au record pour éviter KeyError dans le formatter."""
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
        # Contexte utile (optionnel)
        if record.exc_info:
            base["exc_info"] = self.formatException(record.exc_info)
        return json.dumps(base, ensure_ascii=False)


def _build_handler_stream() -> logging.Handler:
    """Handler stdout par défaut."""
    return logging.StreamHandler(sys.stdout)


def _build_handler_file(path: str) -> logging.Handler:
    """Handler fichier simple (pas de rotation ici pour rester minimal)."""
    # Crée le dossier si besoin
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
    except Exception:
        pass
    return logging.FileHandler(path, encoding="utf-8")


def _apply_common(handler: logging.Handler) -> logging.Handler:
    """Applique filtre + formatter sur un handler."""
    handler.addFilter(SymbolFilter())
    if LOG_JSON:
        fmt = JsonFormatter()
    else:
        fmt = logging.Formatter(
            fmt="%(asctime)s | %(levelname)-5s | %(name)s | [%(symbol)s] %(message)s",
            datefmt=DATEFMT,
        )
    handler.setFormatter(fmt)
    return handler


def get_logger(name: str, symbol: Optional[str] = None) -> logging.Logger:
    """
    Récupère un logger prêt à l'emploi.
    - name: nom logique (ex: 'scanner', 'kucoin.ws', ...)
    - symbol: si fourni, suffixe le nom pour isoler les logs d'un symbole (ex: 'scanner.BTCUSDT')
    """
    logger_name = name if not symbol else f"{name}.{symbol}"
    logger = logging.getLogger(logger_name)

    # Déjà configuré ? On s'assure quand même du niveau.
    if logger.handlers:
        try:
            logger.setLevel(getattr(logging, LOG_LEVEL, logging.INFO))
        except Exception:
            logger.setLevel(logging.INFO)
        return logger

    # Niveau global
    try:
        logger.setLevel(getattr(logging, LOG_LEVEL, logging.INFO))
    except Exception:
        logger.setLevel(logging.INFO)

    # Horodatage UTC optionnel
    if LOG_UTC:
        logging.Formatter.converter = time.gmtime  # type: ignore  # noqa: F821

    # Handler stdout
    handler_stream = _apply_common(_build_handler_stream())
    logger.addHandler(handler_stream)

    # Handler fichier optionnel
    if LOG_FILE:
        try:
            handler_file = _apply_common(_build_handler_file(LOG_FILE))
            logger.addHandler(handler_file)
        except Exception as e:
            # En cas d'échec fichier, on logge sur stdout et on continue
            logger.warning(f"LOG_FILE init failed: {e}")

    # Évite la propagation vers le root logger (pour ne pas doubler les lignes)
    logger.propagate = False
    return logger
