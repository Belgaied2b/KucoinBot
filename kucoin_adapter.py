# -*- coding: utf-8 -*-
"""
kucoin_adapter.py — Pont simple vers KuCoin Futures via KucoinTrader
- Normalise les retours: {"ok": bool, "orderId": str|None, "clientOid": str|None, "code": "...", "msg": "...", "data": {...}, "raw": "..."}
- Tolère les kwargs inattendus (compat moteurs SFI/bridges)
- Gère postOnly → retry auto si "post only" rejeté (ou si ordre devient taker)
"""

import time, math, uuid, httpx
from typing import Optional, Dict, Any

from logger_utils import get_logger
from kucoin_trader import KucoinTrader

log = get_logger("kucoin.adapter")

_TRADER: Optional[KucoinTrader] = None

def _trader() -> KucoinTrader:
    global _TRADER
    if _TRADER is None:
        _TRADER = KucoinTrader()
    return _TRADER

# ---------- META ----------
def get_symbol_meta(symbol: str) -> Dict[str, Any]:
    """
    Essaie d'obtenir le meta depuis /api/v1/contracts/{symbol};
    fallback à /api/v1/contracts/active puis filtre.
    """
    base = _trader().base
    headers = _trader()._headers("GET", "/api/v1/contracts/active")
    try:
        # 1) direct
        path = f"/api/v1/contracts/{symbol}"
        r = httpx.get(base + path, headers=_trader()._headers("GET", path), timeout=5.0)
        if r.status_code == 200:
            js = r.json()
            data = js.get("data") or {}
            if isinstance(data, dict) and data.get("symbol"):
                return data
    except Exception:
        pass

    # 2) active list
    try:
        path = "/api/v1/contracts/active"
        r = httpx.get(base + path, headers=headers, timeout=5.0)
        if r.status_code == 200:
            arr = (r.json() or {}).get("data") or []
            for it in arr:
                if (it or {}).get("symbol") == symbol:
                    return it
    except Exception as e:
        log.warning(f"get_symbol_meta fallback KO: {e}")

    return {}

def _round_to_tick(px: float, tick: float) -> float:
    if not tick or tick <= 0:
        return float(px)
    return math.floor(float(px) / float(tick)) * float(tick)

def _estimate_tick_from_price(px: float) -> float:
    if px >= 100: return 0.1
    if px >= 10:  return 0.01
    if px >= 1:   return 0.001
    if px >= 0.1: return 0.0001
    return 0.00001

def _normalize_ok_json(ok: bool, js: Dict[str, Any]) -> Dict[str, Any]:
    """
    Met à plat orderId/clientOid s'ils sont dans data.
    Ajoute code/msg si présents.
    """
    data = js.get("data") if isinstance(js, dict) else None
    order_id = None
    client_oid = None
    if isinstance(data, dict):
        order_id = data.get("orderId") or data.get("orderid") or data.get("id")
        client_oid = data.get("clientOid") or data.get("clientOidId") or data.get("client_id")

    return {
        "ok": bool(ok),
        "orderId": order_id,
        "clientOid": client_oid,
        "code": js.get("code"),
        "msg": js.get("msg") or js.get("message"),
        "data": data,
        "raw": js,
    }

# ---------- ORDERS ----------
def place_limit_order(
    symbol: str,
    side: str,
    price: float,
    value_usdt: float,
    sl: Optional[float] = None,
    tp1: Optional[float] = None,
    tp2: Optional[float] = None,
    post_only: bool = False,
    **kwargs
) -> Dict[str, Any]:
    """
    Place un LIMIT (postOnly configurable). Si rejet "post only" → retente sans postOnly.
    Retour normalisé avec orderId/clientOid si succès.
    """
    tr = _trader()

    # Tick
    meta = get_symbol_meta(symbol)
    tick = float(meta.get("priceIncrement") or 0.0)
    if tick <= 0:
        tick = _estimate_tick_from_price(price)
    px = _round_to_tick(price, tick)

    client_oid = kwargs.get("client_oid") or kwargs.get("clientOrderId") or kwargs.get("client_order_id")
    if not client_oid:
        client_oid = uuid.uuid4().hex

    # 1) Essai avec postOnly (si demandé)
    ok, js = tr.place_limit(
        symbol=symbol,
        side=side.lower(),
        price=float(px),
        client_oid=client_oid,
        post_only=bool(post_only),
    )
    res = _normalize_ok_json(ok, js)

    # KuCoin peut renvoyer HTTP 200 mais code != "200000" → ok==False ci-dessus
    msg = (res.get("msg") or "").lower()
    is_postonly_reject = ("post only" in msg) or ("post-only" in msg) or ("postonly" in msg)

    if not res["ok"] and post_only and is_postonly_reject:
        log.info(f"[{symbol}] postOnly rejeté → retry sans postOnly (clientOid={client_oid})")
        ok2, js2 = tr.place_limit(
            symbol=symbol,
            side=side.lower(),
            price=float(px),
            client_oid=client_oid,  # on garde le même clientOid
            post_only=False,
        )
        res = _normalize_ok_json(ok2, js2)

    # (Optionnel) Enregistrement d'un SL/TP serveur ici si tu implémentes des OCO séparés.

    return res


def get_order_by_client_oid(client_oid: str) -> Dict[str, Any]:
    """
    Retour JSON KuCoin pour un clientOid; normalise légèrement.
    """
    tr = _trader()
    data = tr.get_order_by_client_oid(client_oid)
    if not data:
        return {"ok": False, "msg": "not found"}
    out = {"ok": True, "data": data}
    if isinstance(data, dict):
        out["orderId"] = data.get("orderId")
        out["clientOid"] = data.get("clientOid")
        out["status"] = data.get("status") or data.get("state")
    return out


def get_order_status(order_id: str) -> Dict[str, Any]:
    """
    Exemple si tu ajoutes plus tard un read par orderId (non strictement nécessaire si tu as clientOid).
    Ici on passe par clientOid uniquement.
    """
    return {"ok": False, "msg": "not_implemented"}


def cancel_order(order_id: str) -> Dict[str, Any]:
    tr = _trader()
    ok, js = tr.cancel(order_id)
    return _normalize_ok_json(ok, js)


def cancel_by_client_oid(client_oid: str) -> Dict[str, Any]:
    tr = _trader()
    ok, js = tr.cancel_by_client_oid(client_oid)
    return _normalize_ok_json(ok, js)
