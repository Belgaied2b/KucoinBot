import os
import time
import hmac
import hashlib
import base64
import json
import requests

# Récupération des variables d'environnement
KUCOIN_API_KEY = os.getenv("KUCOIN_API_KEY")
KUCOIN_API_SECRET = os.getenv("KUCOIN_API_SECRET")
KUCOIN_API_PASSPHRASE = os.getenv("KUCOIN_API_PASSPHRASE")

# 🔍 Debug (à désactiver une fois tout fonctionne)
print("🔐 Vérification des variables API...")
print(f"API_KEY ok: {bool(KUCOIN_API_KEY)}, API_SECRET ok: {bool(KUCOIN_API_SECRET)}, PASSPHRASE ok: {bool(KUCOIN_API_PASSPHRASE)}")

if not KUCOIN_API_KEY or not KUCOIN_API_SECRET or not KUCOIN_API_PASSPHRASE:
    raise ValueError("❌ Variables d’environnement KuCoin manquantes. Vérifie sur Railway.")

BASE_URL = "https://api-futures.kucoin.com"

def generate_signature(endpoint, method, body, timestamp):
    str_to_sign = str(timestamp) + method.upper() + endpoint + (body or "")
    signature = base64.b64encode(
        hmac.new(KUCOIN_API_SECRET.encode("utf-8"), str_to_sign.encode("utf-8"), hashlib.sha256).digest()
    ).decode()
    return signature

def get_headers(endpoint, method="POST", body=None):
    timestamp = str(int(time.time() * 1000))
    body_str = json.dumps(body) if body else ""
    signature = generate_signature(endpoint, method, body_str, timestamp)

    passphrase = base64.b64encode(
        hmac.new(KUCOIN_API_SECRET.encode("utf-8"), KUCOIN_API_PASSPHRASE.encode("utf-8"), hashlib.sha256).digest()
    ).decode()

    return {
        "KC-API-KEY": KUCOIN_API_KEY,
        "KC-API-SIGN": signature,
        "KC-API-TIMESTAMP": timestamp,
        "KC-API-PASSPHRASE": passphrase,
        "KC-API-KEY-VERSION": "2",
        "Content-Type": "application/json"
    }

def place_order(symbol, side, entry_price):
    try:
        endpoint = "/api/v1/orders"
        url = BASE_URL + endpoint

        order_data = {
            "clientOid": str(int(time.time() * 1000)),
            "symbol": symbol,
            "side": side.lower(),
            "leverage": 3,
            "type": "limit",
            "price": str(entry_price),
            "size": str(round(20 / float(entry_price), 3)),  # 🔧 sécurité float
            "timeInForce": "GTC"
        }

        headers = get_headers(endpoint, "POST", order_data)
        response = requests.post(url, headers=headers, json=order_data)

        if response.status_code != 200:
            print(f"❌ Erreur HTTP KuCoin {symbol} : {response.status_code} - {response.text}")
            return None

        data = response.json()

        if data.get("code") == "200000":
            print(f"✅ Ordre LIMIT placé ({side.upper()}) sur {symbol} @ {entry_price}")
            return data["data"].get("orderId")
        else:
            print(f"❌ Échec ordre KuCoin {symbol}: {data}")
            return None

    except Exception as e:
        print(f"❌ Exception place_order : {e}")
        return None
