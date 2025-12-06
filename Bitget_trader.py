# bitget_trader.py — Exécution ordres institutionnels (USDT-M Perp)
# Compatible reduce-only, SL, TP, modify-stop & BE monitor.

import time
import logging
from typing import Optional, Dict, Any

from bitget_utils import bitget_request, get_contract_info

LOGGER = logging.getLogger(__name__)

# ================================================================
# 🔧 HELPERS
# ================================================================
def _round_to_tick(x: float, tick: float) -> float:
    if tick <= 0:
        return float(x)
    steps = round(float(x) / float(tick))
    return round(steps * float(tick), 12)


def _opposite(side: str) -> str:
    side = (side or "").lower()
    return "sell" if side == "buy" else "buy"


# ================================================================
# 📈 PLACE LIMIT ORDER (ENTRY)
# ================================================================
def place_limit_order(
    symbol: str,
    side: str,
    price: float,
    size_lots: float,
    reduce_only: bool = False
) -> Dict[str, Any]:

    side = side.lower()
    volume = float(size_lots)

    body = {
        "symbol": symbol,
        "productType": "umcbl",
        "orderType": "limit",
        "side": side,
        "price": str(price),
        "size": str(volume),
        "reduceOnly": str(reduce_only).lower(),
        "marginMode": "cross"
    }

    r = bitget_request("POST", "/api/mix/v1/order/placeOrder", body=body, auth=True)

    if r.get("code") != "00000":
        LOGGER.error("[BITGET] place_limit_order ERROR %s: %s", symbol, r)
        return {"ok": False, "resp": r}

    return {"ok": True, "resp": r}


# ================================================================
# 🛑 STOP LOSS — reduce-only STOP
# ================================================================
def place_reduce_only_stop(
    symbol: str,
    side: str,
    new_stop: float,
    size_lots: float,
) -> Dict[str, Any]:

    side = side.lower()
    volume = float(size_lots)

    body = {
        "symbol": symbol,
        "productType": "umcbl",
        "orderType": "market",       # SL is a STOP-Market
        "triggerType": "fill_price",
    }

    # === STOP direction ===
    if side == "buy":
        body["side"] = "sell"
        body["triggerPrice"] = str(new_stop)
        body["size"] = str(volume)
    else:
        body["side"] = "buy"
        body["triggerPrice"] = str(new_stop)
        body["size"] = str(volume)

    body["reduceOnly"] = "true"
    body["marginMode"] = "cross"

    r = bitget_request("POST", "/api/mix/v1/order/placePlanOrder", body=body, auth=True)

    if r.get("code") != "00000":
        LOGGER.error("[BITGET] SL ERROR %s: %s", symbol, r)
        return {"ok": False, "resp": r}

    return {"ok": True, "resp": r}


# ================================================================
# 🎯 TAKE PROFIT LIMIT — reduce-only
# ================================================================
def place_reduce_only_tp_limit(
    symbol: str,
    side: str,
    take_profit: float,
    size_lots: float
):
    opp = _opposite(side)

    body = {
        "symbol": symbol,
        "productType": "umcbl",
        "orderType": "limit",
        "side": opp,
        "price": str(take_profit),
        "size": str(size_lots),
        "reduceOnly": "true",
        "marginMode": "cross"
    }

    r = bitget_request("POST", "/api/mix/v1/order/placeOrder", body=body, auth=True)

    if r.get("code") != "00000":
        LOGGER.error("[BITGET] TP LIMIT ERROR %s: %s", symbol, r)
        return {"ok": False, "resp": r}

    return {"ok": True, "resp": r}


# ================================================================
# 🔄 MODIFY STOP LOSS — update stop trigger
# ================================================================
def modify_stop_order(
    symbol: str,
    side: str,
    existing_order_id: Optional[str],
    new_stop: float,
    size_lots: float
):
    """
    Bitget: pour modifier un STOP, on doit CANCEL + RECREER.
    Ici on recrée un STOP propre (simple et robuste).
    """

    LOGGER.info("[BITGET] modify_stop -> recreate SL %s @ %.12f", symbol, new_stop)

    # on pose un nouveau SL reduce-only
    return place_reduce_only_stop(
        symbol=symbol,
        side=side,
        new_stop=new_stop,
        size_lots=size_lots,
    )


# ================================================================
# ❌ CANCEL ORDER
# ================================================================
def cancel_order(symbol: str, order_id: str) -> Dict[str, Any]:

    body = {
        "symbol": symbol,
        "orderId": order_id,
        "productType": "umcbl"
    }

    r = bitget_request("POST", "/api/mix/v1/order/cancelOrder", body=body, auth=True)

    if r.get("code") != "00000":
        LOGGER.error("[BITGET] cancel_order ERROR %s: %s", symbol, r)
        return {"ok": False, "resp": r}

    return {"ok": True, "resp": r}


# ================================================================
# 📜 LIST OPEN ORDERS
# ================================================================
def list_open_orders(symbol: str):
    params = {"symbol": symbol, "productType": "umcbl"}
    r = bitget_request("GET", "/api/mix/v1/order/ordersPending", params=params, auth=True)
    return r.get("data", []) if isinstance(r, dict) else []


# ================================================================
# 📊 POSITION INFO
# ================================================================
def get_open_position(symbol: str):
    params = {"symbol": symbol, "productType": "umcbl"}
    r = bitget_request("GET", "/api/mix/v1/position/singlePosition", params=params, auth=True)
    try:
        return r.get("data", {}) or {}
    except Exception:
        return {}


# ================================================================
# 💲 MARK PRICE fallback
# ================================================================
def get_mark_price(symbol: str) -> float:
    params = {"symbol": symbol, "productType": "umcbl"}
    r = bitget_request("GET", "/api/mix/v1/market/markPrice", params=params, auth=False)
    try:
        return float(r.get("data", {}).get("markPrice", 0))
    except Exception:
        return 0.0
