import httpx
from config import SETTINGS

def send_msg(text: str):
    if not SETTINGS.tg_token or not SETTINGS.tg_chat: return
    try:
        url=f"https://api.telegram.org/bot{SETTINGS.tg_token}/sendMessage"
        httpx.post(url, json={"chat_id": SETTINGS.tg_chat, "text": text}, timeout=6.0)
    except: pass
