"""
exits.py — stop-loss / take-profit avec fallback automatique
1) Essaye /api/v1/stopOrders (Futures)
2) Si 400007 (permissions) ou autre 4xx bloquant -> fallback /api/v1/orders avec champs stop*
- Arrondit stopPrice/price au tickSize du contrat
- reduceOnly=True
"""
import time, uuid, logging, requests, base64, hmac, hashlib, json
from typing import Literal, Dict, Any, Tuple

from settings import KUCOIN_API_KEY, KUCOIN_API_SECRET, KUCOIN_API_PASSPHRASE, LEVERAGE
from kucoin_utils import get_contract_info

LOGGER = logging.getLogger(__name__)

BASE = "https://api-futures.kucoin.com"
STOP_EP = "/api/v1/stopOrders"
ORDERS_EP = "/api/v1/orders"

# -------- auth helpers --------
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
    ts = int(time.time()*1000)
    sig, pph = _sign(ts, "POST", ep, body)
    return requests.post(BASE+ep, headers=_headers(ts, sig, pph), json=body, timeout=timeout)

def _safe_json(resp: requests.Response) -> dict:
    try:
        return resp.json()
    except Exception:
        return {"raw": resp.text}

def _round_to_tick(x: float, tick: float) -> float:
    if tick <= 0: return x
    steps = int(x / tick)
    return round(steps * tick, 8)

def _stop_direction(side: str) -> Tuple[str, str]:
    """Retourne (stop, tp_stop) pour KuCoin Futures."""
    side = side.lower()
    # Pour un LONG (entrée buy): SL doit se déclencher "down", TP se déclare via stopPriceType="TP" côté "up".
    if side == "buy":
        return "down", "up"
    # Pour un SHORT (entrée sell): SL "up", TP "down".
    return "up", "down"

def _is_permission_error(resp: requests.Response, data: dict) -> bool:
    if resp is None: return False
    if resp.status_code == 401 or resp.status_code == 403:
        return True
    code = str(data.get("code", ""))
    # 400007 = Access denied, require more permission.
    return code == "400007"

# -------- public API --------
def place_stop_loss(symbol: str, side: Literal["buy","sell"], size_lots: int, stop_price: float) -> dict:
    """
    STOP au marché (reduce-only). Fallback auto si stopOrders refuse.
    """
    meta = get_contract_info(symbol)
    tick = float(meta.get("tickSize", 0.01))
    stop_price = _round_to_tick(float(stop_price), tick)
    stop_dir, _tp_dir = _stop_direction(side)

    # 1) Tentative /stopOrders
    body = {
        "clientOid": str(uuid.uuid4()),
        "symbol": symbol,
        "side": "sell" if side=="buy" else "buy",
        "type": "market",
        "size": str(int(size_lots)),
        "stop": stop_dir,
        "stopPrice": f"{stop_price:.8f}",
        "reduceOnly": True,
        "leverage": str(int(LEVERAGE)),
    }
    r = _auth_post(STOP_EP, body)
    data = _safe_json(r)

    if r.status_code == 200:
        return {"ok": True, "endpoint": "stopOrders", "data": data}

    # 4xx permission -> fallback
    if _is_permission_error(r, data) or (400 <= r.status_code < 500):
        LOGGER.warning("stopOrders refused (%s): %s -> fallback /orders stop*", r.status_code, data)
        # 2) Fallback /orders avec champs stop*
        fb = {
            "clientOid": str(uuid.uuid4()),
            "symbol": symbol,
            "side": "sell" if side=="buy" else "buy",
            "type": "market",
            "size": str(int(size_lots)),
            "reduceOnly": True,
            "stop": stop_dir,
            "stopPrice": f"{stop_price:.8f}",
            "leverage": str(int(LEVERAGE)),
        }
        rr = _auth_post(ORDERS_EP, fb)
        d2 = _safe_json(rr)
        if rr.status_code == 200:
            return {"ok": True, "endpoint": "orders(stop*)", "data": d2}
        return {"ok": False, "endpoint": "orders(stop*)", "status": rr.status_code, "body": d2}

    # autre erreur
    return {"ok": False, "endpoint": "stopOrders", "status": r.status_code, "body": data}

def place_take_profit(symbol: str, side: Literal["buy","sell"], size_lots: int, tp_price: float) -> dict:
    """
    TAKE-PROFIT limit (reduce-only). Fallback auto si stopOrders refuse.
    """
    meta = get_contract_info(symbol)
    tick = float(meta.get("tickSize", 0.01))
    tp_price = _round_to_tick(float(tp_price), tick)
    _sl_dir, tp_dir = _stop_direction(side)

    # 1) Tentative /stopOrders
    body = {
        "clientOid": str(uuid.uuid4()),
        "symbol": symbol,
        "side": "sell" if side=="buy" else "buy",
        "type": "limit",
        "price": f"{tp_price:.8f}",
        "size": str(int(size_lots)),
        "stop": tp_dir,
        "stopPriceType": "TP",
        "reduceOnly": True,
        "leverage": str(int(LEVERAGE)),
        "timeInForce": "GTC",
    }
    r = _auth_post(STOP_EP, body)
    data = _safe_json(r)
    if r.status_code == 200:
        return {"ok": True, "endpoint": "stopOrders", "data": data}

    # 4xx permission -> fallback
    if _is_permission_error(r, data) or (400 <= r.status_code < 500):
        LOGGER.warning("stopOrders TP refused (%s): %s -> fallback /orders stop*", r.status_code, data)
        fb = {
            "clientOid": str(uuid.uuid4()),
            "symbol": symbol,
            "side": "sell" if side=="buy" else "buy",
            "type": "limit",
            "price": f"{tp_price:.8f}",
            "size": str(int(size_lots)),
            "reduceOnly": True,
            "stop": tp_dir,
            "stopPriceType": "TP",
            "stopPrice": f"{tp_price:.8f}",  # KuCoin requiert parfois stopPrice même pour TP
            "leverage": str(int(LEVERAGE)),
            "timeInForce": "GTC",
        }
        rr = _auth_post(ORDERS_EP, fb)
        d2 = _safe_json(rr)
        if rr.status_code == 200:
            return {"ok": True, "endpoint": "orders(stop*)", "data": d2}
        return {"ok": False, "endpoint": "orders(stop*)", "status": rr.status_code, "body": d2}

    return {"ok": False, "endpoint": "stopOrders", "status": r.status_code, "body": data}
