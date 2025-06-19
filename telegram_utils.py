import os
import requests

BOT_TOKEN = os.getenv("TOKEN")
CHAT_ID = os.getenv("CHAT_ID")

def send_signal_to_telegram(message, image_path=None):
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
    payload = {
        "chat_id": CHAT_ID,
        "text": message,
        "parse_mode": "HTML"
    }

    # 🖼 Envoi image si dispo
    if image_path:
        photo_url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendPhoto"
        with open(image_path, "rb") as photo:
            files = {"photo": photo}
            data = {"chat_id": CHAT_ID, "caption": message, "parse_mode": "HTML"}
            response = requests.post(photo_url, data=data, files=files)
            return response.status_code == 200

    # 📝 Envoi message texte seul
    response = requests.post(url, data=payload)
    return response.status_code == 200
