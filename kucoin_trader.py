"""
kucoin_trader.py — LIMIT orders en lots + modes auto (compatible 4 arguments)
- Calcule 'size' (lots) si size_lots n'est pas fourni, à partir de MARGIN_USDT * LEVERAGE et des specs contrat.
- Aligne les modes avant l'ordre :
    * PositionMode: One-Way (0) par défaut (modifiable)
    * MarginMode (par symbole): ISOLATED par défaut (modifiable)
- Arrondit le prix au tickSize du contrat.
- Envoie clientOid (UUID v4).
- ok=True UNIQUEMENT si data.code == "200000".
"""
from __future__ import annotations
import time, hmac, json, uuid, base64, hashlib, logging
from typing import Dict, Any, Tuple, Optional, List
import requests

from settings import (
    KUCOIN_API_KEY, KUCOIN_API_SECRET, KUCOIN_API_PASSPHRASE,
    MARGIN_USDT, LEVERAGE,
)
from retry_utils import backoff_retry, TransientHTTPError
from kucoin_utils import get_contract_info

LOGGER = logging.getLogger(__name__)

BASE = "https://api-futures.kucoin.com"

# Endpoints
ORDERS_EP = "/api/v1/orders"
GET_POSITION_MODE_EP = "/api/v2/position/getPositionMode"
SWITCH_POSITION_MODE_EP = "/api/v2/position/switchPositionMode"
SWITCH_MARGIN_MODE_EP = "/api/v2/position/changeMarginMode"
GET_POSITION_EP = "/api/v1/position"
GET_OPEN_ORDERS_EP = "/api/v1/openOrders"  # peut renvoyer 404 selon comptes/régions
LIST_ORDERS_EP = "/api/v1/orders"          # fallback: ?status=active&symbol=...

DEFAULT_POSITION_MODE = "0"       # "0"=one-way, "1"=hedge
DEFAULT_MARGIN_MODE = "ISOLATED"  # "CROSS" si tu préfères


# ---------- Auth helpers ----------
def _sign(ts_ms: int, method: str, endpoint: str, body: dict | None) -> Tuple[bytes, bytes]:
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

def _safe_json(resp: requests.Response) -> dict:
    try:
        return resp.json()
    except Exception:
        return {"raw": resp.text}

def _needs_retry(status_code: int) -> bool:
    return status_code >= 500 or status_code == 429

def _auth_post(endpoint: str, body: dict, timeout=12) -> requests.Response:
    ts = int(time.time() * 1000)
    sig, pph = _sign(ts, "POST", endpoint, body)
    headers = _headers(ts, sig, pph)
    return requests.post(BASE + endpoint, headers=headers, json=body, timeout=timeout)

def _auth_get(endpoint: str, params=None, timeout=12) -> requests.Response:
    ts = int(time.time() * 1000)
    sig, pph = _sign(ts, "GET", endpoint, None)
    headers = _headers(ts, sig, pph)
    return requests.get(BASE + endpoint, headers=headers, params=params, timeout=timeout)

def _auth_delete(endpoint: str, timeout=12) -> requests.Response:
    ts = int(time.time() * 1000)
    sig, pph = _sign(ts, "DELETE", endpoint, None)
    headers = _headers(ts, sig, pph)
    return requests.delete(BASE + endpoint, headers=headers, timeout=timeout)


# ---------- Modes helpers ----------
def _ensure_position_mode(target_mode: str = DEFAULT_POSITION_MODE) -> None:
    try:
        r = _auth_get(GET_POSITION_MODE_EP)
        if r.status_code != 200:
            LOGGER.warning("GetPositionMode status=%s body=%s", r.status_code, r.text)
            return
        cur = _safe_json(r).get("data", {}).get("positionMode")
        if str(cur) != str(target_mode):
            LOGGER.info("Switch positionMode %s -> %s", cur, target_mode)
            rr = _auth_post(SWITCH_POSITION_MODE_EP, {"positionMode": str(target_mode)})
            if rr.status_code != 200:
                LOGGER.error("SwitchPositionMode failed %s: %s", rr.status_code, rr.text)
    except Exception as e:
        LOGGER.exception("ensure_position_mode error: %s", e)

def _ensure_margin_mode(symbol: str, target_mode: str = DEFAULT_MARGIN_MODE) -> None:
    try:
        rr = _auth_post(SWITCH_MARGIN_MODE_EP, {"symbol": symbol, "marginMode": target_mode})
        if rr.status_code != 200:
            LOGGER.warning("SwitchMarginMode %s => %s failed %s: %s", symbol, target_mode, rr.status_code, rr.text)
    except Exception as e:
        LOGGER.exception("ensure_margin_mode error: %s", e)


# ---------- Sizing helpers ----------
def _round_price(price: float, tick: float) -> float:
    if tick <= 0:
        return price
    steps = int(price / tick)
    return round(steps * tick, 8)

def _compute_lots_for_value(price: float, multiplier: float, lot_size: int, budget_notional: float) -> int:
    if price <= 0 or multiplier <= 0:
        return lot_size
    notional_per_lot = price * multiplier
    est = int(budget_notional / max(notional_per_lot, 1e-12))
    return max(lot_size, est)


# ---------- Place order (compat 3 ou 4 arguments) ----------
@backoff_retry(exceptions=(TransientHTTPError, requests.RequestException))
def place_limit_order(symbol: str, side: str, price: float,
                      size_lots: Optional[int] = None, *, post_only: bool = True) -> dict:
    if not KUCOIN_API_KEY or not KUCOIN_API_SECRET or not KUCOIN_API_PASSPHRASE:
        LOGGER.error("KuCoin API credentials missing.")
        return {"ok": False, "error": "missing_api_credentials"}

    meta = get_contract_info(symbol)
    lot_size = int(meta.get("lotSize", 1))
    multiplier = float(meta.get("multiplier", 1.0))
    tick = float(meta.get("tickSize", 0.01))
    adj_price = _round_price(float(price), tick)

    if size_lots is None or int(size_lots) <= 0:
        budget = float(MARGIN_USDT) * float(LEVERAGE)
        size_lots = _compute_lots_for_value(adj_price, multiplier, lot_size, budget)
    else:
        size_lots = max(lot_size, int(size_lots))

    _ensure_position_mode(DEFAULT_POSITION_MODE)
    _ensure_margin_mode(symbol, DEFAULT_MARGIN_MODE)

    ts = int(time.time() * 1000)
    client_oid = str(uuid.uuid4())
    body = {
        "clientOid": client_oid,
        "symbol": symbol,
        "side": side.lower(),
        "type": "limit",
        "price": f"{adj_price:.8f}",
        "size": str(int(size_lots)),
        "leverage": str(int(LEVERAGE)),
        "timeInForce": "GTC",
        "postOnly": bool(post_only),
    }

    resp = _auth_post(ORDERS_EP, body)
    if _needs_retry(resp.status_code):
        raise TransientHTTPError(f"KuCoin transient {resp.status_code}: {resp.text}")

    data = _safe_json(resp)
    ok = (resp.status_code == 200 and str(data.get("code")) == "200000")
    if not ok:
        LOGGER.error("KuCoin order error %s %s -> %s", resp.status_code, symbol, data)
        return {
            "ok": False, "status": resp.status_code, "body": data,
            "clientOid": client_oid, "price": adj_price, "size_lots": size_lots,
        }

    return {"ok": True, "data": data, "clientOid": client_oid, "price": adj_price, "size_lots": size_lots}


# ----------------------------------------------------------------------
# === Positions / Open orders ===
# ----------------------------------------------------------------------
def get_open_position(symbol: str) -> Dict[str, Any]:
    try:
        r = _auth_get(GET_POSITION_EP, params={"symbol": symbol})
        if r.status_code != 200:
            return {}
        return r.json().get("data") or {}
    except Exception:
        return {}

def list_open_orders(symbol: str) -> List[Dict[str, Any]]:
    """
    Essaye d’abord /openOrders (peut renvoyer 404), sinon fallback sur /orders?status=active.
    Retourne une liste homogène d’ordres ouverts.
    """
    # tentative 1 : /openOrders
    try:
        r1 = _auth_get(GET_OPEN_ORDERS_EP, params={"symbol": symbol})
        if r1.status_code == 200:
            d = r1.json().get("data") or {}
            items = d.get("items") or d.get("orderList") or []
            return items if isinstance(items, list) else []
        else:
            LOGGER.warning("openOrders %s -> %s %s", symbol, r1.status_code, r1.text)
    except Exception as e:
        LOGGER.warning("openOrders error %s: %s", symbol, e)

    # fallback : /orders?status=active
    try:
        r2 = _auth_get(LIST_ORDERS_EP, params={"status": "active", "symbol": symbol})
        if r2.status_code != 200:
            LOGGER.warning("orders(active) %s -> %s %s", symbol, r2.status_code, r2.text)
            return []
        d = r2.json().get("data") or {}
        items = d.get("items") or d.get("orderList") or []
        return items if isinstance(items, list) else []
    except Exception as e:
        LOGGER.warning("orders(active) error %s: %s", symbol, e)
        return []


# ----------------------------------------------------------------------
# === Reduce-only SL / TP (robustes) ===
# ----------------------------------------------------------------------
def place_reduce_only_stop(symbol: str, side: str, new_stop: float, size_lots: int,
                           stop_price_type: str = "MP") -> Dict[str, Any]:
    """
    Place un stop-loss reduce-only.
    - 1er essai : stop-market.
    - Si 'stopInvalid' / 400100 -> 2e essai en stop-limit (price = stopPrice, postOnly False).
    """
    stop_side = "sell" if side.lower() == "buy" else "buy"

    # essai #1 : stop-market
    body1 = {
        "clientOid": str(uuid.uuid4()),
        "symbol": symbol,
        "side": stop_side,
        "type": "market",
        "reduceOnly": True,
        "stop": "loss",
        "stopPriceType": stop_price_type,   # MP = mark price (par défaut)
        "stopPrice": f"{float(new_stop):.8f}",
        "size": str(int(size_lots)),
    }
    ts = int(time.time() * 1000)
    sig, pph = _sign(ts, "POST", ORDERS_EP, body1)
    r1 = requests.post(BASE + ORDERS_EP, headers=_headers(ts, sig, pph), json=body1, timeout=12)
    data1 = _safe_json(r1)

    if r1.status_code == 200 and str(data1.get("code")) == "200000":
        return {"ok": True, "status": 200, "data": data1}

    # si rejet -> essai #2 : stop-limit (souvent accepté quand stop-market est invalidé)
    body2 = {
        "clientOid": str(uuid.uuid4()),
        "symbol": symbol,
        "side": stop_side,
        "type": "limit",
        "reduceOnly": True,
        "stop": "loss",
        "stopPriceType": stop_price_type,
        "stopPrice": f"{float(new_stop):.8f}",
        "price": f"{float(new_stop):.8f}",
        "size": str(int(size_lots)),
        "timeInForce": "GTC",
        "postOnly": False
    }
    ts2 = int(time.time() * 1000)
    sig2, pph2 = _sign(ts2, "POST", ORDERS_EP, body2)
    r2 = requests.post(BASE + ORDERS_EP, headers=_headers(ts2, sig2, pph2), json=body2, timeout=12)
    data2 = _safe_json(r2)

    return {"ok": (r2.status_code == 200 and str(data2.get("code")) == "200000"),
            "status": r2.status_code, "data": data2}

def place_reduce_only_tp_limit(symbol: str, side: str, take_profit: float, size_lots: int) -> Dict[str, Any]:
    tp_side = "sell" if side.lower() == "buy" else "buy"
    body = {
        "clientOid": str(uuid.uuid4()),
        "symbol": symbol,
        "side": tp_side,
        "type": "limit",
        "reduceOnly": True,
        "price": f"{float(take_profit):.8f}",
        "size": str(int(size_lots)),
        "timeInForce": "GTC",
        "postOnly": True
    }
    ts = int(time.time() * 1000)
    sig, pph = _sign(ts, "POST", ORDERS_EP, body)
    r = requests.post(BASE + ORDERS_EP, headers=_headers(ts, sig, pph), json=body, timeout=12)
    data = _safe_json(r)

    # si refus (ex. postOnly traverse), on retente sans postOnly
    if not (r.status_code == 200 and str(data.get("code")) == "200000"):
        body["clientOid"] = str(uuid.uuid4())
        body["postOnly"] = False
        ts2 = int(time.time() * 1000)
        sig2, pph2 = _sign(ts2, "POST", ORDERS_EP, body)
        r2 = requests.post(BASE + ORDERS_EP, headers=_headers(ts2, sig2, pph2), json=body, timeout=12)
        data2 = _safe_json(r2)
        return {"ok": (r2.status_code == 200 and str(data2.get("code")) == "200000"),
                "status": r2.status_code, "data": data2}

    return {"ok": True, "status": 200, "data": data}


# ----------------------------------------------------------------------
# === Cancel / Modify (pour BE & trailing) ===
# ----------------------------------------------------------------------
def cancel_order(order_id: str) -> Dict[str, Any]:
    try:
        r = _auth_delete(f"/api/v1/orders/{order_id}")
        return {"ok": r.status_code == 200, "status": r.status_code, "data": _safe_json(r)}
    except Exception as e:
        LOGGER.exception("cancel_order error: %s", e)
        return {"ok": False, "error": str(e)}

def modify_stop_order(symbol: str, side: str, existing_order_id: Optional[str],
                      new_stop: float, size_lots: int, stop_price_type: str = "MP") -> Dict[str, Any]:
    """
    Modifie un stop en le *remplaçant* (cancel + nouvelle création).
    Si existing_order_id est fourni, on tente de le cancel d'abord.
    """
    try:
        if existing_order_id:
            _ = cancel_order(existing_order_id)
            time.sleep(0.15)
    except Exception:
        pass
    return place_reduce_only_stop(symbol, side, new_stop=new_stop, size_lots=size_lots, stop_price_type=stop_price_type)
