# telegram_notifier.py
import os, logging, httpx
log = logging.getLogger("tg")
BOT = os.getenv("TELEGRAM_BOT_TOKEN", "")
CHAT= os.getenv("TELEGRAM_CHAT_ID", "")
def send(text: str):
    if not BOT or not CHAT:
        log.info("[TG OFF] %s", text); return
    try:
        url=f"https://api.telegram.org/bot{BOT}/sendMessage"
        httpx.post(url, json={"chat_id": CHAT, "text": text, "parse_mode":"Markdown",
                              "disable_web_page_preview": True}, timeout=10)
    except Exception as e:
        log.warning("tg send KO: %s", e)
