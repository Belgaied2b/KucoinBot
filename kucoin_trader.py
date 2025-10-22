"""
kucoin_trader.py — KuCoin Futures LIMIT orders avec clientOid obligatoire
- clientOid: UUID v4 unique
- valueQty: marge fixe en USDT (paramétrable via settings.MARGIN_USDT)
- leverage: paramétrable via settings.LEVERAGE
- postOnly: True (évite l'exécution en taker non désirée)
- timeInForce: GTC
- retries uniquement sur 5xx; 4xx renvoyés tels quels
"""
import os
import time
import hmac
import json
import uuid
import base64
import hashlib
import logging
import requests

from settings import KUCOIN_API_KEY, KUCOIN_API_SECRET, KUCOIN_API_PASSPHRASE, MARGIN_USDT, LEVERAGE
from retry_utils import backoff_retry, TransientHTTPError

LOGGER = logging.getLogger(__name__)

BASE = "https://api-futures.kucoin.com"
ORDERS_EP = "/api/v1/orders"


def _sign(ts_ms: int, method: str, endpoint: str, body: dict | None) -> tuple[bytes, bytes]:
    payload = str(ts_ms) + method.upper() + endpoint + (json.dumps(body) if body else "")
    sig = base64.b64encode(hmac.new(KUCOIN_API_SECRET.encode(), payload.encode(), hashlib.sha256).digest())
    pph = base64.b64encode(hmac.new(KUCOIN_API_SECRET.encode(), KUCOIN_API_PASSPHRASE.encode(), hashlib.sha256).digest())
    return sig, pph


def _headers(ts_ms: int, sig: bytes, pph: bytes) -> dict:
    return {
        "KC-API-SIGN": sig,
        "KC-API-TIMESTAMP": str(ts_ms),
        "KC-API-KEY": KUCOIN_API_KEY,
        "KC-API-PASSPHRASE": pph,
        "KC-API-KEY-VERSION": "2",
        "Content-Type": "application/json",
    }


def _needs_retry(status_code: int) -> bool:
    # Retry uniquement 5xx
    return status_code >= 500


@backoff_retry(exceptions=(TransientHTTPError, requests.RequestException))
def place_limit_order(symbol: str, side: str, price: float) -> dict:
    """
    Place un ordre LIMIT Futures KuCoin avec marge fixe (valueQty = MARGIN_USDT).
    side: "buy" ou "sell" (insensible à la casse)
    """
    if not KUCOIN_API_KEY or not KUCOIN_API_SECRET or not KUCOIN_API_PASSPHRASE:
        LOGGER.error("KuCoin API credentials missing.")
        return {"ok": False, "error": "missing_api_credentials"}

    ts = int(time.time() * 1000)
    client_oid = str(uuid.uuid4())

    body = {
        "clientOid": client_oid,
        "symbol": symbol,                  # ex: "XBTUSDTM"
        "side": side.lower(),              # "buy" / "sell"
        "type": "limit",
        "price": f"{price:.8f}",
        "valueQty": str(int(MARGIN_USDT)), # marge fixe en USDT
        "leverage": str(int(LEVERAGE)),
        "timeInForce": "GTC",
        "postOnly": True,
        # "reduceOnly": False,             # décommente si besoin
        # "remark": "inst-top1",           # tag optionnel
    }

    sig, pph = _sign(ts, "POST", ORDERS_EP, body)
    headers = _headers(ts, sig, pph)

    url = BASE + ORDERS_EP
    resp = requests.post(url, headers=headers, json=body, timeout=12)

    # Gestion retry / erreurs
    if _needs_retry(resp.status_code):
        raise TransientHTTPError(f"KuCoin 5xx {resp.status_code}: {resp.text}")

    # 4xx -> renvoyer tel quel pour debug (clientOid manquant, params invalides, etc.)
    if resp.status_code != 200:
        LOGGER.error("KuCoin order error %s %s -> %s", resp.status_code, symbol, resp.text)
        return {"ok": False, "status": resp.status_code, "body": _safe_json(resp)}

    data = _safe_json(resp)
    return {"ok": True, "data": data, "clientOid": client_oid}


def _safe_json(resp: requests.Response) -> dict:
    try:
        return resp.json()
    except Exception:
        return {"raw": resp.text}
