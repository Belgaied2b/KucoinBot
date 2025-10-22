"""
exits.py — stop-loss / take-profit avec fallback robuste
- 1) Tente /api/v1/stopOrders
- 2) Si permissions/4xx -> fallback /api/v1/orders avec champs stop*
- Ajouts:
    * stopPriceType configurables (TP=last trade, MP=mark)
    * SL fallback: envoie explicitement stopPriceType
    * ok=True SEULEMENT si data.code == "200000"
    * Arrondi au tick + reduceOnly
"""
import time, uuid, logging, requests, base64, hmac, hashlib, json
from typing import Literal, Tuple

from settings import (
    KUCOIN_API_KEY, KUCOIN_API_SECRET, KUCOIN_API_PASSPHRASE, LEVERAGE,
    STOP_TRIGGER_TYPE_SL, STOP_TRIGGER_TYPE_TP
)
from kucoin_utils import get_contract_info

LOGGER = logging.getLogger(__name__)

BASE = "https://api-futures.kucoin.com"
STOP_EP = "/api/v1/stopOrders"
ORDERS_EP = "/api/v1/orders"


# ---------------- auth helpers ----------------
def _sign(ts, method, ep, body):
    payload = str(ts) + method.upper() + ep + (json.dumps(body) if body else "")
    sig = base64.b64encode(hmac.new(KUCOIN_API_SECRET.encode(), payload.encode(), hashlib.sha256).digest())
    pph = base64.b64encode(hmac.new(KUCOIN_API_SECRET.encode(), KUCOIN_API_PASSPHRASE.encode(), hashlib.sha256).digest())
    return sig, pph

def _headers(ts, sig, pph):
    return {
        "KC-API-SIGN": sig,
        "KC-API-TIMESTAMP": str(ts),
        "KC-API-KEY": KUCOIN_API_KEY,
        "KC-API-PASSPHRASE": pph,
        "KC-API-KEY-VERSION": "2",
        "Content-Type": "application/json",
    }

def _auth_post(ep: str, body: dict, timeout=12) -> requests.Response:
    ts = int(time.time() * 1000)
    sig, pph = _sign(ts, "POST", ep, body)
    return requests.post(BASE + ep, headers=_headers(ts, sig, pph), json=body, timeout=timeout)

def _safe_json(resp: requests.Response) -> dict:
    try:
        return resp.json()
    except Exception:
        return {"raw": resp.text}

def _round_to_tick(x: float, tick: float) -> float:
    if tick <= 0:
        return x
    steps = int(float(x) / tick)
    return round(steps * tick, 8)

def _stop_direction(side: str) -> Tuple[str, str]:
    side = side.lower()
    if side == "buy":      # position longue -> SL en "down", TP en "up"
        return "down", "up"
    return "up", "down"    # position courte


def _is_permission_error(resp: requests.Response, data: dict) -> bool:
    if resp is None:
        return False
    if resp.status_code in (401, 403):
        return True
    return str(data.get("code", "")) == "400007"

def _ok(data: dict) -> bool:
    # KuCoin renvoie parfois HTTP 200 avec un code d'erreur applicatif
    return isinstance(data, dict) and str(data.get("code")) == "200000"


# ---------------- public API ----------------
def place_stop_loss(symbol: str, side: Literal["buy", "sell"], size_lots: int, stop_price: float) -> dict:
    """
    STOP-LOSS au marché (reduce-only). Fallback si stopOrders refusé.
    """
    meta = get_contract_info(symbol)
    tick = float(meta.get("tickSize", 0.01))
    stop_price = _round_to_tick(float(stop_price), tick)
    stop_dir, _tp_dir = _stop_direction(side)

    # 1) Endpoint recommandé: /stopOrders
    body = {
        "clientOid": str(uuid.uuid4()),
        "symbol": symbol,
        "side": "sell" if side == "buy" else "buy",
        "type": "market",
        "size": str(int(size_lots)),
        "stop": stop_dir,
        "stopPrice": f"{stop_price:.8f}",
        "stopPriceType": STOP_TRIGGER_TYPE_SL,  # <-- EXPLICITE: "TP" (last) ou "MP" (mark)
        "reduceOnly": True,
        "leverage": str(int(LEVERAGE)),
    }
    r = _auth_post(STOP_EP, body)
    d = _safe_json(r)
    if r.status_code == 200 and _ok(d):
        return {"ok": True, "endpoint": "stopOrders", "data": d}

    # 2) Fallback: /orders avec champs stop*
    if _is_permission_error(r, d) or (400 <= r.status_code < 500):
        LOGGER.warning("stopOrders SL refused (%s): %s -> fallback /orders stop*", r.status_code, d)
        fb = {
            "clientOid": str(uuid.uuid4()),
            "symbol": symbol,
            "side": "sell" if side == "buy" else "buy",
            "type": "market",
            "size": str(int(size_lots)),
            "reduceOnly": True,
            "stop": stop_dir,
            "stopPrice": f"{stop_price:.8f}",
            "stopPriceType": STOP_TRIGGER_TYPE_SL,  # <-- OBLIGATOIRE en fallback aussi
            "leverage": str(int(LEVERAGE)),
        }
        rr = _auth_post(ORDERS_EP, fb)
        d2 = _safe_json(rr)
        if rr.status_code == 200 and _ok(d2):
            return {"ok": True, "endpoint": "orders(stop*)", "data": d2}
        return {"ok": False, "endpoint": "orders(stop*)", "status": rr.status_code, "body": d2}

    return {"ok": False, "endpoint": "stopOrders", "status": r.status_code, "body": d}


def place_take_profit(symbol: str, side: Literal["buy", "sell"], size_lots: int, tp_price: float) -> dict:
    """
    TAKE-PROFIT limit (reduce-only). Fallback si stopOrders refusé.
    """
    meta = get_contract_info(symbol)
    tick = float(meta.get("tickSize", 0.01))
    tp_price = _round_to_tick(float(tp_price), tick)
    _sl_dir, tp_dir = _stop_direction(side)

    # 1) /stopOrders
    body = {
        "clientOid": str(uuid.uuid4()),
        "symbol": symbol,
        "side": "sell" if side == "buy" else "buy",
        "type": "limit",
        "price": f"{tp_price:.8f}",
        "size": str(int(size_lots)),
        "stop": tp_dir,
        "stopPriceType": STOP_TRIGGER_TYPE_TP,  # "TP" ou "MP"
        "stopPrice": f"{tp_price:.8f}",         # souvent requis pour TP aussi
        "reduceOnly": True,
        "leverage": str(int(LEVERAGE)),
        "timeInForce": "GTC",
    }
    r = _auth_post(STOP_EP, body)
    d = _safe_json(r)
    if r.status_code == 200 and _ok(d):
        return {"ok": True, "endpoint": "stopOrders", "data": d}

    # 2) Fallback: /orders stop*
    if _is_permission_error(r, d) or (400 <= r.status_code < 500):
        LOGGER.warning("stopOrders TP refused (%s): %s -> fallback /orders stop*", r.status_code, d)
        fb = {
            "clientOid": str(uuid.uuid4()),
            "symbol": symbol,
            "side": "sell" if side == "buy" else "buy",
            "type": "limit",
            "price": f"{tp_price:.8f}",
            "size": str(int(size_lots)),
            "reduceOnly": True,
            "stop": tp_dir,
            "stopPriceType": STOP_TRIGGER_TYPE_TP,
            "stopPrice": f"{tp_price:.8f}",
            "leverage": str(int(LEVERAGE)),
            "timeInForce": "GTC",
        }
        rr = _auth_post(ORDERS_EP, fb)
        d2 = _safe_json(rr)
        if rr.status_code == 200 and _ok(d2):
            return {"ok": True, "endpoint": "orders(stop*)", "data": d2}
        return {"ok": False, "endpoint": "orders(stop*)", "status": rr.status_code, "body": d2}

    return {"ok": False, "endpoint": "stopOrders", "status": r.status_code, "body": d}
