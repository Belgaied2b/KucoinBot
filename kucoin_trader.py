import time, hmac, base64, json, hashlib, requests, os, logging
from settings import KUCOIN_API_KEY, KUCOIN_API_SECRET, KUCOIN_API_PASSPHRASE, MARGIN_USDT, LEVERAGE
from retry_utils import backoff_retry, TransientHTTPError

LOGGER = logging.getLogger(__name__)
BASE = "https://api-futures.kucoin.com"

def _sign(ts: int, method: str, endpoint: str, body: dict):
    str_to_sign = str(ts) + method + endpoint + (json.dumps(body) if body else "")
    sig = base64.b64encode(hmac.new(KUCOIN_API_SECRET.encode(), str_to_sign.encode(), hashlib.sha256).digest())
    pph = base64.b64encode(hmac.new(KUCOIN_API_SECRET.encode(), KUCOIN_API_PASSPHRASE.encode(), hashlib.sha256).digest())
    return sig, pph

@backoff_retry(exceptions=(TransientHTTPError, requests.RequestException))
def place_limit_order(symbol: str, side: str, price: float):
    ts = int(time.time() * 1000)
    endpoint = "/api/v1/orders"
    body = {
        "symbol": symbol,
        "side": side.lower(),    # buy/sell
        "type": "limit",
        "price": str(price),
        "valueQty": str(int(MARGIN_USDT)),  # marge fixe USDT
        "leverage": str(int(LEVERAGE))
    }
    sig, pph = _sign(ts, "POST", endpoint, body)
    headers = {
        "KC-API-SIGN": sig, "KC-API-TIMESTAMP": str(ts),
        "KC-API-KEY": KUCOIN_API_KEY, "KC-API-PASSPHRASE": pph,
        "KC-API-KEY-VERSION": "2", "Content-Type": "application/json"
    }
    url = BASE + endpoint
    r = requests.post(url, headers=headers, json=body, timeout=12)
    if r.status_code >= 500:
        raise TransientHTTPError(f"KuCoin 5xx {r.status_code}")
    if r.status_code != 200:
        LOGGER.error("KuCoin error %s: %s", r.status_code, r.text)
    return r.json()
