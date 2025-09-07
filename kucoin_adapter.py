# -*- coding: utf-8 -*-
"""
kucoin_adapter.py — Couche d'adaptation simple autour de KucoinTrader
- place_limit_order avec gestion postOnly → retry en taker si rejet
- get_symbol_meta (tick, lot)
- get_order_by_client_oid (vérif serveur)
"""

from __future__ import annotations
import time, httpx, math
from typing import Dict, Any, Optional

from config import SETTINGS
from logger_utils import get_logger
from kucoin_trader import KucoinTrader

log = get_logger("kucoin.adapter")

_trader: Optional[KucoinTrader] = None
def _get_trader() -> KucoinTrader:
    global _trader
    if _trader is None:
        _trader = KucoinTrader()
    return _trader

def get_symbol_meta(symbol: str) -> Dict[str, Any]:
    """
    Retourne la meta contrat KuCoin Futures:
      priceIncrement, lotSize, multiplier, baseCurrency, etc.
    """
    try:
        url = SETTINGS.kucoin_base_url + f"/api/v1/contracts/{symbol}"
        r = httpx.get(url, timeout=6.0)
        if r.status_code == 200:
            js = r.json() or {}
            return js.get("data") or {}
        log.warning("[get_symbol_meta] HTTP=%s body=%s", r.status_code, r.text[:200])
        return {}
    except Exception as e:
        log.error("[get_symbol_meta] exception: %s", e)
        return {}

def _normalize_ok_json(js: Dict[str, Any]) -> Dict[str, Any]:
    """
    Normalise la réponse KuCoin {code, data, msg} → {ok, code, msg, orderId?, clientOid?}
    """
    if not isinstance(js, dict):
        return {"ok": False, "code": None, "msg": "bad json", "raw": js}

    code = js.get("code")
    msg  = js.get("msg")
    data = js.get("data") or {}

    ok = (code == "200000")
    orderId   = data.get("orderId")
    clientOid = data.get("clientOid")

    out = {"ok": ok, "code": code, "msg": msg, "orderId": orderId, "clientOid": clientOid, "data": data}
    return out

# Messages/indices qui suggèrent un rejet post-only (ordre aurait exécuté au marché)
_POST_ONLY_HINTS = (
    "post only", "post-only", "would be executed immediately", "immediately in the market",
    "taker", "liquidity taking", "taker order"
)
# Codes d'échec courants (liste non exhaustive)
_FAIL_CODES_RETRY_TAKER = set(["100001", "100005", "100006", "100400"])

def place_limit_order(
    symbol: str,
    side: str,
    price: float,
    value_usdt: float,
    sl: float,
    tp1: float,
    tp2: float,
    post_only: bool = True,
) -> Dict[str, Any]:
    """
    Tente un LIMIT maker (postOnly). Si rejet (code/msg), retente sans postOnly (IOC par défaut côté trader si tu veux).
    Retournera {ok, code, msg, orderId?, clientOid?, data?, raw?}
    """
    tr = _get_trader()

    ok, js = tr.place_limit(symbol=symbol, side=("buy" if side == "long" else "sell"), price=float(price),
                            post_only=bool(post_only))
    res = _normalize_ok_json(js if js else {})
    log.info("[place_limit] maker try → ok=%s code=%s msg=%s clientOid=%s orderId=%s",
             res.get("ok"), res.get("code"), res.get("msg"), res.get("clientOid"), res.get("orderId"))

    # Succès "clean"
    if res.get("ok"):
        return res

    # Si postOnly et rejet par code/msg → retry en taker (postOnly=False)
    msg = (res.get("msg") or "").lower()
    code = (res.get("code") or "")
    retry_taker = False
    if post_only:
        if any(h in msg for h in _POST_ONLY_HINTS):
            retry_taker = True
        elif code and code in _FAIL_CODES_RETRY_TAKER:
            retry_taker = True

    if retry_taker:
        log.info("[place_limit] retry as taker (postOnly=False)")
        ok2, js2 = tr.place_limit(symbol=symbol, side=("buy" if side == "long" else "sell"),
                                  price=float(price), post_only=False)
        res2 = _normalize_ok_json(js2 if js2 else {})
        log.info("[place_limit] taker try → ok=%s code=%s msg=%s clientOid=%s orderId=%s",
                 res2.get("ok"), res2.get("code"), res2.get("msg"),
                 res2.get("clientOid"), res2.get("orderId"))
        return res2

    # Rien passé proprement → renvoyer ce qu'on a
    return res

def get_order_by_client_oid(client_oid: str) -> Optional[Dict[str, Any]]:
    """
    Retourne l'ordre (status) par clientOid si disponible.
    """
    tr = _get_trader()
    od = tr.get_order_by_client_oid(client_oid)
    return od
