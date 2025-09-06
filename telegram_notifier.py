import httpx
from config import SETTINGS
from logger_utils import get_logger

log = get_logger("telegram")

def send_msg(text: str):
    if not SETTINGS.tg_token or not SETTINGS.tg_chat:
        log.debug("telegram disabled or missing vars")
        return
    try:
        url=f"https://api.telegram.org/bot{SETTINGS.tg_token}/sendMessage"
        r = httpx.post(url, json={"chat_id": SETTINGS.tg_chat, "text": text}, timeout=6.0)
        if r.status_code != 200:
            log.warning(f"telegram send failed {r.status_code}: {r.text[:200]}")
    except Exception as e:
        log.warning(f"telegram exception: {e}")
