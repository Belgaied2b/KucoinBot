import os
import time
import hmac
import hashlib
import base64
import json
import requests

# 📌 Configuration KuCoin Futures
KUCOIN_API_KEY = "6890cfb4dffe710001e6edb0"
KUCOIN_API_SECRET = "889e4492-c2ff-4c9d-9136-64afe6d5e780"
KUCOIN_API_PASSPHRASE = "Nad1703-_"
BASE_URL = "https://api-futures.kucoin.com"

# ✅ DEBUG : Vérification des variables
print("🔐 Vérification des variables API...")
print(f"API_KEY ok: {bool(KUCOIN_API_KEY)}, API_SECRET ok: {bool(KUCOIN_API_SECRET)}, PASSPHRASE ok: {bool(KUCOIN_API_PASSPHRASE)}")

if not KUCOIN_API_KEY or not KUCOIN_API_SECRET or not KUCOIN_API_PASSPHRASE:
    raise ValueError("❌ Variables d’environnement KuCoin manquantes. Vérifie sur Railway.")

# 🔐 Génère la signature pour l'authentification
def generate_signature(endpoint, method, body, timestamp):
    str_to_sign = str(timestamp) + method.upper() + endpoint + (body or "")
    signature = base64.b64encode(
        hmac.new(KUCOIN_API_SECRET.encode("utf-8"), str_to_sign.encode("utf-8"), hashlib.sha256).digest()
    ).decode()
    return signature

# 📦 Prépare les headers
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

# 📈 Place un ordre MARKET sur Futures KuCoin
def place_order(symbol, side, entry_price):
    try:
        endpoint = "/api/v1/orders"
        url = BASE_URL + endpoint

        # Taille en contrats (entier requis par l’API)
        size = int(20 / float(entry_price))

        order_data = {
            "clientOid": str(int(time.time() * 1000)),
            "symbol": symbol,
            "side": side.lower(),        # "buy" ou "sell"
            "leverage": 3,
            "type": "market",            # Ordre MARKET
            "size": str(size)            # en contrats (string entière)
        }

        headers = get_headers(endpoint, "POST", order_data)
        response = requests.post(url, headers=headers, json=order_data)

        if response.status_code != 200:
            print(f"❌ Erreur HTTP KuCoin {symbol} : {response.status_code} - {response.text}")
            return None

        data = response.json()

        if data.get("code") == "200000":
            print(f"✅ Ordre MARKET placé ({side.upper()}) sur {symbol} @ {entry_price} | Size: {size}")
            return data["data"].get("orderId")
        else:
            print(f"❌ Échec ordre KuCoin {symbol}: {data}")
            return None

    except Exception as e:
        print(f"❌ Exception place_order : {e}")
        return None
