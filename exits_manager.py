"""
exits_manager.py — exits propres:
- purge_reduce_only(symbol): annule les anciens ordres reduce-only ouverts (évite accumulation)
- attach_exits_after_fill(...): pose SL/TP APRES confirmation de fill
S'appuie sur exits.py (fallback stopOrders -> orders(stop*)).
"""
from __future__ import annotations
import requests, json, base64, hmac, hashlib, time
from typing import Literal, Tuple

from exits import place_stop_loss, place_take_profit
from settings import KUCOIN_API_KEY, KUCOIN_API_SECRET, KUCOIN_API_PASSPHRASE

BASE = "https://api-futures.kucoin.com"

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

def _list_reduce_only(symbol: str):
    ts = int(time.time()*1000)
    ep = f"/api/v1/orders?symbol={symbol}&status=open"
    sig, pph = _sign(ts, "GET", ep, None)
    r = requests.get(BASE+ep, headers=_headers(ts, sig, pph), timeout=8)
    try:
        items = r.json().get("data", {}).get("items", [])
    except Exception:
        items = []
    return [o for o in items if str(o.get("reduceOnly")).lower() == "true"]

def _cancel(order_id: str):
    ts = int(time.time()*1000)
    ep = f"/api/v1/orders/{order_id}"
    sig, pph = _sign(ts, "DELETE", ep, None)
    requests.delete(BASE+ep, headers=_headers(ts, sig, pph), timeout=8)

def purge_reduce_only(symbol: str):
    for o in _list_reduce_only(symbol):
        try: _cancel(o.get("id"))
        except Exception: pass

def attach_exits_after_fill(symbol: str, side: Literal["buy","sell"], df, entry: float, sl: float, tp: float, lots: int) -> Tuple[dict, dict]:
    """
    Pose SL (market reduce-only) + TP (limit reduce-only) maintenant que l'entrée est (au moins) partiellement remplie.
    """
    sl_resp = place_stop_loss(symbol, side, lots, sl)
    tp_resp = place_take_profit(symbol, side, lots, tp)
    return sl_resp, tp_resp
