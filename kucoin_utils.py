# bitget_utils.py — Base REST Bitget Futures USDT-M (retry, auth, public, klines)

import time
import hmac
import hashlib
import logging
import requests
import json
from typing import Any, Dict, Optional, List, Union

LOGGER = logging.getLogger(__name__)

# ================================================================
# 🔑 ENV / CONFIG
# ================================================================
BITGET_API_KEY = ""
BITGET_API_SECRET = ""
BITGET_API_PASSPHRASE = ""

BASE_URL = "https://api.bitget.com"

# retry simple
MAX_RETRIES = 5
RETRY_DELAY = 0.35

# ================================================================
# 🔧 SIGNATURE
# ================================================================
def sign(message: str, secret: str) -> str:
    return hmac.new(
        secret.encode("utf-8"),
        message.encode("utf-8"),
        hashlib.sha256
    ).hexdigest()

def _timestamp_ms():
    return str(int(time.time() * 1000))


# ================================================================
# 🔧 REQUEST CORE
# ================================================================
def bitget_request(
    method: str,
    path: str,
    params: Optional[dict] = None,
    body: Optional[dict] = None,
    auth: bool = False
) -> Any:

    url = BASE_URL + path
    params = params or {}
    body = body or {}

    for retry in range(1, MAX_RETRIES + 1):

        try:
            ts = _timestamp_ms()

            headers = {
                "Content-Type": "application/json",
            }

            if auth:
                # message = timestamp + method + path + body_json
                body_json = json.dumps(body) if body else ""
                msg = ts + method.upper() + path + body_json
                sig = sign(msg, BITGET_API_SECRET)

                headers.update({
                    "ACCESS-KEY": BITGET_API_KEY,
                    "ACCESS-SIGN": sig,
                    "ACCESS-PASSPHRASE": BITGET_API_PASSPHRASE,
                    "ACCESS-TIMESTAMP": ts,
                })

            if method.upper() == "GET":
                r = requests.get(url, params=params, headers=headers, timeout=8)
            elif method.upper() == "POST":
                r = requests.post(url, params=params, data=json.dumps(body), headers=headers, timeout=8)
            else:
                raise ValueError(f"Unsupported method {method}")

            # Too many requests → retry
            if r.status_code == 429:
                LOGGER.warning(f"[BITGET] 429 rate-limited {path}, retry {retry}/{MAX_RETRIES}")
                time.sleep(RETRY_DELAY * retry)
                continue

            # network error
            if r.status_code >= 500:
                LOGGER.warning(f"[BITGET] server error {r.status_code} {path}, retry {retry}")
                time.sleep(RETRY_DELAY * retry)
                continue

            data = r.json()
            return data

        except Exception as e:
            LOGGER.warning(f"[BITGET] request error {path} retry {retry}: {e}")
            time.sleep(RETRY_DELAY * retry)

    raise RuntimeError(f"[BITGET] request failed after retries: {path}")


# ================================================================
# 📡 PUBLIC API
# ================================================================
def get_klines(symbol: str, granularity: str = "1m", limit: int = 200) -> List[dict]:
    """
    Bitget Futures klines:
    granularity = 1m, 5m, 15m, 1h, 4h, 1d …
    """
    path = "/api/mix/v1/market/candles"
    params = {
        "symbol": symbol,
        "granularity": granularity,
        "limit": limit
    }
    r = bitget_request("GET", path, params=params, auth=False)
    # format returned: [timestamp, open, high, low, close, volume, …]
    if not isinstance(r, list):
        return []
    out = []
    for c in r:
        out.append({
            "ts": int(c[0]),
            "open": float(c[1]),
            "high": float(c[2]),
            "low": float(c[3]),
            "close": float(c[4]),
            "volume": float(c[5]),
        })
    return list(reversed(out))  # oldest → newest


def fetch_all_symbols() -> List[str]:
    """Retourne tous les PERP USDT-M disponibles."""
    path = "/api/mix/v1/market/contracts"
    params = {"productType": "umcbl"}  # USDT-M Perp
    r = bitget_request("GET", path, params=params)
    out = []
    try:
        for s in r.get("data", []):
            out.append(s["symbol"])
    except Exception:
        pass
    return out


def get_contract_info(symbol: str) -> Dict[str, Any]:
    """Tick size, min size, precision, etc."""
    path = "/api/mix/v1/market/contracts"
    params = {"productType": "umcbl"}
    r = bitget_request("GET", path, params=params)

    try:
        for s in r.get("data", []):
            if s["symbol"] == symbol:
                return {
                    "symbol": symbol,
                    "tickSize": float(s["priceEndStep"]),
                    "minSize": float(s["minTradeNum"]),
                    "sizeStep": float(s["volumePlace"]),
                    "contractSize": 1.0  # bitget = 1 by default
                }
    except Exception:
        pass

    return {
        "symbol": symbol,
        "tickSize": 0.01,
        "minSize": 0.001,
        "sizeStep": 1.0,
        "contractSize": 1.0
    }
