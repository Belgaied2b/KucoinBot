"""
kucoin_trader.py
Place des ordres LIMIT avec marge fixe de 20 USDT.
"""
import time
import hmac
import base64
import json
import hashlib
import requests
import os

KUCOIN_API = os.getenv("KUCOIN_API_KEY")
KUCOIN_SECRET = os.getenv("KUCOIN_API_SECRET")
KUCOIN_PASSPHRASE = os.getenv("KUCOIN_API_PASSPHRASE")

def place_limit_order(symbol, side, price):
    now = int(time.time() * 1000)
    endpoint = "/api/v1/orders"
    url = "https://api-futures.kucoin.com" + endpoint
    body = {
        "symbol": symbol,
        "side": side,
        "type": "limit",
        "price": str(price),
        "valueQty": "20",
        "leverage": "20"
    }
    str_to_sign = str(now) + "POST" + endpoint + json.dumps(body)
    signature = base64.b64encode(hmac.new(KUCOIN_SECRET.encode(), str_to_sign.encode(), hashlib.sha256).digest())
    passphrase = base64.b64encode(hmac.new(KUCOIN_SECRET.encode(), KUCOIN_PASSPHRASE.encode(), hashlib.sha256).digest())
    headers = {
        "KC-API-SIGN": signature,
        "KC-API-TIMESTAMP": str(now),
        "KC-API-KEY": KUCOIN_API,
        "KC-API-PASSPHRASE": passphrase,
        "KC-API-KEY-VERSION": "2",
        "Content-Type": "application/json"
    }
    r = requests.post(url, headers=headers, json=body)
    return r.json()
