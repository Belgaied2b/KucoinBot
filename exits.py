"""
exits.py — pose des ordres stop/take (reduce-only) juste après l'entrée.
NB: KuCoin Futures stop/TP passent par des "stopOrders" séparés.
"""
import time, uuid, logging, requests, base64, hmac, hashlib, json
from typing import Literal
from settings import KUCOIN_API_KEY, KUCOIN_API_SECRET, KUCOIN_API_PASSPHRASE, LEVERAGE
LOGGER = logging.getLogger(__name__)

BASE = "https://api-futures.kucoin.com"
STOP_EP = "/api/v1/stopOrders"

def _sign(ts, method, ep, body):
    payload = str(ts) + method + ep + (json.dumps(body) if body else "")
    sig = base64.b64encode(hmac.new(KUCOIN_API_SECRET.encode(), payload.encode(), hashlib.sha256).digest())
    pph = base64.b64encode(hmac.new(KUCOIN_API_SECRET.encode(), KUCOIN_API_PASSPHRASE.encode(), hashlib.sha256).digest())
    return sig, pph

def _headers(ts, sig, pph):
    return {"KC-API-SIGN": sig, "KC-API-TIMESTAMP": str(ts), "KC-API-KEY": KUCOIN_API_KEY,
            "KC-API-PASSPHRASE": pph, "KC-API-KEY-VERSION": "2", "Content-Type":"application/json"}

def _post(ep, body):
    ts = int(time.time()*1000)
    sig, pph = _sign(ts, "POST", ep, body)
    return requests.post(BASE+ep, headers=_headers(ts,sig,pph), json=body, timeout=12)

def place_stop_loss(symbol: str, side: Literal["buy","sell"], size_lots: int, stop_price: float) -> dict:
    """
    Crée un STOP reduce-only au marché quand le prix touche stop_price.
    """
    body = {
        "clientOid": str(uuid.uuid4()),
        "symbol": symbol,
        "side": "sell" if side=="buy" else "buy",   # inverse
        "type": "market",
        "size": str(int(size_lots)),
        "stop": "down" if side=="buy" else "up",
        "stopPrice": f"{stop_price:.8f}",
        "reduceOnly": True,
        "leverage": str(int(LEVERAGE)),
    }
    r = _post(STOP_EP, body)
    try: return r.json()
    except Exception: return {"raw": r.text}

def place_take_profit(symbol: str, side: Literal["buy","sell"], size_lots: int, tp_price: float) -> dict:
    """
    Crée un TAKE-PROFIT limit reduce-only.
    """
    body = {
        "clientOid": str(uuid.uuid4()),
        "symbol": symbol,
        "side": "sell" if side=="buy" else "buy",
        "type": "limit",
        "price": f"{tp_price:.8f}",
        "size": str(int(size_lots)),
        "stop": "up" if side=="buy" else "down",
        "stopPriceType": "TP",
        "reduceOnly": True,
        "leverage": str(int(LEVERAGE)),
    }
    r = _post(STOP_EP, body)
    try: return r.json()
    except Exception: return {"raw": r.text}
