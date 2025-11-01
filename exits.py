"""
exits.py — DÉPRÉCIÉ : délègue aux fonctions robustes de kucoin_trader.
Objectif : unifier le placement des SL/TP pour qu'ils soient visibles dans /api/v1/orders?status=open
et que le monitor BE fonctionne de façon cohérente.

API conservée :
- place_stop_loss(symbol, side, size_lots, stop_price) -> dict
- place_take_profit(symbol, side, size_lots, tp_price) -> dict
"""

from __future__ import annotations
import logging
from typing import Literal

from kucoin_trader import (
    place_reduce_only_stop,
    place_reduce_only_tp_limit,
)
from kucoin_utils import get_contract_info  # si d'autres modules importent depuis ici

LOGGER = logging.getLogger(__name__)

def place_stop_loss(symbol: str, side: Literal["buy", "sell"], size_lots: int, stop_price: float) -> dict:
    """
    STOP-LOSS reduce-only (via /api/v1/orders avec champs stop* gérés par kucoin_trader).
    Retourne un dict compatible avec l'ancien exits.py.
    """
    resp = place_reduce_only_stop(symbol, side, new_stop=float(stop_price), size_lots=int(size_lots))
    if resp.get("ok"):
        return {"ok": True, "endpoint": "orders", "data": resp.get("data")}
    return {"ok": False, "endpoint": "orders", "status": resp.get("status"), "data": resp.get("data")}

def place_take_profit(symbol: str, side: Literal["buy", "sell"], size_lots: int, tp_price: float) -> dict:
    """
    TAKE-PROFIT LIMIT reduce-only (via /api/v1/orders).
    Retourne un dict compatible avec l'ancien exits.py.
    """
    resp = place_reduce_only_tp_limit(symbol, side, take_profit=float(tp_price), size_lots=int(size_lots))
    if resp.get("ok"):
        return {"ok": True, "endpoint": "orders", "data": resp.get("data")}
    return {"ok": False, "endpoint": "orders", "status": resp.get("status"), "data": resp.get("data")}
