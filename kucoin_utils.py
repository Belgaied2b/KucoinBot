# kucoin_utils.py

import os
import httpx
import pandas as pd
import time
import logging
import io
import base64
from config import TOKEN, CHAT_ID

logger = logging.getLogger(__name__)

# Kucoin Futures REST
BASE_URL = "https://api-futures.kucoin.com"

# Telegram Bot HTTP API
BASE_TELEGRAM_URL = f"https://api.telegram.org/bot{TOKEN}"

def get_kucoin_perps():
    url = f"{BASE_URL}/api/v1/contracts/active"
    resp = httpx.get(url)
    resp.raise_for_status()
    data = resp.json()
    if data.get("code") not in ("200000", None):
        logger.error(f"❌ get_kucoin_perps → code={data['code']} msg={data.get('msg')}")
        return []
    return [c["symbol"] for c in data.get("data", [])]

def fetch_klines(symbol, interval="4hour", limit=150):
    granularity_map = {"4hour": 240}
    minutes = granularity_map.get(interval, 240)
    url = f"{BASE_URL}/api/v1/kline/query"
    params = {"symbol": symbol, "granularity": minutes, "limit": limit}

    resp = httpx.get(url, params=params)
    resp.raise_for_status()
    data = resp.json()
    if data.get("code") != "200000":
        raise ValueError(f"{symbol} → code={data.get('code')} msg={data.get('msg')}")

    raw = data.get("data", [])
    if not raw:
        raise ValueError(f"{symbol} → pas de données {interval} disponibles")

    first = raw[0]
    if len(first) == 7:
        cols = ["timestamp","open","high","low","close","volume","turnover"]
    elif len(first) == 6:
        cols = ["timestamp","open","high","low","close","volume"]
    else:
        raise ValueError(f"{symbol} → format inattendu: {len(first)} colonnes")

    df = pd.DataFrame(raw, columns=cols)
    df["timestamp"] = pd.to_datetime(df["timestamp"], unit="ms")
    df.set_index("timestamp", inplace=True)
    df = df.astype(float)

    time.sleep(0.2)
    logger.info(f"✅ {symbol} : {len(df)} bougies {interval} récupérées")
    return df

def send_telegram(text: str, image_b64: str = None):
    """
    Envoie un message ou une photo (encodée en base64) via Telegram.
    """
    if image_b64:
        # Préparer l'image
        img_bytes = base64.b64decode(image_b64)
        files = {
            "photo": ("chart.png", img_bytes, "image/png")
        }
        data = {
            "chat_id": CHAT_ID,
            "caption": text,
            "parse_mode": "Markdown"
        }
        resp = httpx.post(f"{BASE_TELEGRAM_URL}/sendPhoto", data=data, files=files)
    else:
        payload = {
            "chat_id": CHAT_ID,
            "text": text,
            "parse_mode": "Markdown"
        }
        resp = httpx.post(f"{BASE_TELEGRAM_URL}/sendMessage", json=payload)

    try:
        resp.raise_for_status()
    except Exception as e:
        logger.error(f"❌ Erreur Telegram: {e} — réponse: {resp.text}")

def get_account_balance(symbol: str) -> float:
    """
    Récupère le solde à risquer pour le sizing.
    Par défaut, lit l'ENV ACCOUNT_BALANCE (sinon 1000).
    Vous pouvez surcharger cette fonction pour aller chercher votre vrai solde via l'API Kucoin.
    """
    return float(os.getenv("ACCOUNT_BALANCE", "1000"))
