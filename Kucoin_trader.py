import os
import time
import hmac
import hashlib
import base64
import json
import requests

KUCOIN_API_KEY = os.getenv("KUCOIN_API_KEY")
KUCOIN_API_SECRET = os.getenv("KUCOIN_API_SECRET")
KUCOIN_API_PASSPHRASE = os.getenv("KUCOIN_API_PASSPHRASE")

BASE_URL = "https://api-futures.kucoin.com"

def generate_signature(endpoint, method, body, timestamp):
    str_to_sign = str(timestamp) + method.upper() + endpoint + (body or "")
    signature = base64.b64encode(
        hmac.new(KUCOIN_API_SECRET.encode(), str_to_sign.encode(), hashlib.sha256).digest()
    ).decode()
    return signature

def get_headers(endpoint, method="POST", body=None):
    timestamp = str(int(time.time() * 1000))
    body_str = json.dumps(body) if body else ""
    signature = generate_signature(endpoint, method, body_str, timestamp)

    passphrase = base64.b64encode(
        hmac.new(KUCOIN_API_SECRET.encode(), KUCOIN_API_PASSPHRASE.encode(), hashlib.sha256).digest()
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
            "size": str(round(20 / entry_price, 3)),
            "timeInForce": "GTC"
        }

        headers = get_headers(endpoint, "POST", order_data)
        response = requests.post(url, headers=headers, json=order_data)
        data = response.json()

        if data.get("code") == "200000":
            print(f"✅ Ordre LIMIT placé ({side.upper()}) sur {symbol} @ {entry_price}")
            return data["data"]["orderId"]
        else:
            print(f"❌ Échec ordre KuCoin {symbol}: {data}")
            return None
    except Exception as e:
        print(f"❌ Exception place_order : {e}")
        return None
