import logging
import requests
from settings import TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID
from retry_utils import backoff_retry, TransientHTTPError

LOGGER = logging.getLogger(__name__)
BASE = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}"

@backoff_retry(exceptions=(TransientHTTPError, requests.RequestException))
def send_telegram_message(text: str, parse_mode: str = "Markdown"):
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        LOGGER.warning("Telegram not configured, skip message.")
        return {"ok": False, "skipped": True}
    url = f"{BASE}/sendMessage"
    resp = requests.post(url, json={"chat_id": TELEGRAM_CHAT_ID, "text": text, "parse_mode": parse_mode}, timeout=10)
    if resp.status_code >= 500:
        raise TransientHTTPError(f"Telegram 5xx: {resp.text}")
    if resp.status_code != 200:
        LOGGER.error("Telegram error %s: %s", resp.status_code, resp.text)
    return resp.json()
