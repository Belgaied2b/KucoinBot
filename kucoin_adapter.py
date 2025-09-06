# -*- coding: utf-8 -*-
# kucoin_adapter.py — expose des helpers module-level attendus par execution_sfi/scanner,
# via la classe KucoinTrader de ton projet. Utilise l'API publique en fallback.

from __future__ import annotations
import time, logging
from typing import Optional, Dict, Any

try:
    import requests
except Exception:
    requests = None  # on gèrera le cas

try:
    from kucoin_trader import KucoinTrader
except Exception as e:
    raise RuntimeError(f"kucoin_adapter: impossible d'importer KucoinTrader: {e}")

_CLIENT = None
def _client() -> KucoinTrader:
    global _CLIENT
    if _CLIENT is None:
        _CLIENT = KucoinTrader()
    return _CLIENT

def get_symbol_meta(symbol: str) -> Dict[str, Any]:
    c = _client()
    for attr in ("get_symbol_meta", "symbol_meta", "get_meta"):
        if hasattr(c, attr):
            try:
                m = getattr(c, attr)(symbol) if callable(getattr(c, attr)) else getattr(c, attr).get(symbol)
                if isinstance(m, dict) and ("priceIncrement" in m or "tickSize" in m):
                    inc = float(m.get("priceIncrement") or m.get("tickSize") or 0.01)
                    return {"priceIncrement": inc}
            except Exception:
                pass
    if requests:
        try:
            url = f"https://api-futures.kucoin.com/api/v1/contracts/active?symbol={symbol}"
            r = requests.get(url, timeout=5.0)
            if r.status_code == 200:
                data = (r.json() or {}).get("data") or {}
                inc = float(data.get("tickSize") or data.get("priceIncrement") or 0.01)
                return {"priceIncrement": inc}
        except Exception:
            pass
    return {"priceIncrement": 0.01}

def get_orderbook_top(symbol: str) -> Dict[str, Any]:
    c = _client()
    for attr in ("get_orderbook_top", "best_top", "top"):
        if hasattr(c, attr):
            try:
                top = getattr(c, attr)(symbol) if callable(getattr(c, attr)) else getattr(c, attr).get(symbol)
                if isinstance(top, dict):
                    bb = top.get("bestBid") or top.get("best_bid")
                    ba = top.get("bestAsk") or top.get("best_ask")
                    bs = top.get("bidSize"); asz = top.get("askSize")
                    bb = float(bb) if bb is not None else None
                    ba = float(ba) if ba is not None else None
                    bs = float(bs) if bs is not None else None
                    asz= float(asz) if asz is not None else None
                    return {"bestBid": bb, "bestAsk": ba, "bidSize": bs, "askSize": asz}
            except Exception:
                pass
    if requests:
        try:
            url = f"https://api-futures.kucoin.com/api/v1/ticker?symbol={symbol}"
            r = requests.get(url, timeout=5.0)
            if r.status_code == 200:
                d = (r.json() or {}).get("data") or {}
                bb = float(d.get("bestBidPrice")) if d.get("bestBidPrice") else None
                ba = float(d.get("bestAskPrice")) if d.get("bestAskPrice") else None
                bs = float(d.get("bestBidSize")) if d.get("bestBidSize") else None
                asz= float(d.get("bestAskSize")) if d.get("bestAskSize") else None
                return {"bestBid": bb, "bestAsk": ba, "bidSize": bs, "askSize": asz}
        except Exception:
            pass
    return {"bestBid": None, "bestAsk": None, "bidSize": None, "askSize": None}

def place_limit_order(symbol: str, side: str, price: float, value_usdt: float,
                      sl: Optional[float]=None, tp1: Optional[float]=None, tp2: Optional[float]=None,
                      post_only: bool=True, client_order_id: Optional[str]=None, extra_kwargs: Optional[dict]=None) -> Dict[str, Any]:
    c = _client()
    side = "buy" if side.lower() == "long" else "sell"
    candidates = [
        ("open_limit_post_only_value", {"symbol": symbol, "side": side, "price": price, "valueQty": value_usdt}),
        ("open_limit_post_only",       {"symbol": symbol, "side": side, "price": price, "valueQty": value_usdt}),
        ("open_limit_value",           {"symbol": symbol, "side": side, "price": price, "valueQty": value_usdt, "postOnly": post_only}),
        ("open_limit",                 {"symbol": symbol, "side": side, "price": price, "valueQty": value_usdt, "postOnly": post_only}),
        ("place_limit",                {"symbol": symbol, "side": side, "price": price, "valueQty": value_usdt, "postOnly": post_only}),
    ]
    for name, kwargs in candidates:
        if hasattr(c, name):
            try:
                resp = getattr(c, name)(**kwargs)
                return resp if isinstance(resp, dict) else {"status": "ok", "raw": str(resp)}
            except Exception:
                continue
    if hasattr(c, "_post"):
        body = {"clientOid": client_order_id or f"sig_{int(time.time()*1000)}",
                "symbol": symbol, "side": side, "price": price, "valueQty": value_usdt,
                "type": "limit", "postOnly": post_only}
        try:
            return c._post("/api/v1/orders", body)
        except Exception:
            pass
    raise RuntimeError("kucoin_adapter: aucune méthode LIMIT compatible trouvée")

def place_order(**kwargs):          return place_limit_order(**kwargs)
def place_limit_valueqty(**kwargs): return place_limit_order(**kwargs)

def cancel_order(order_id: str):
    c = _client()
    for name in ("cancel", "cancel_order"):
        if hasattr(c, name):
            return getattr(c, name)(order_id)
    raise RuntimeError("kucoin_adapter: cancel indisponible")

def replace_order(order_id: str, new_price: float):
    c = _client()
    for name in ("replace", "amend", "modify"):
        if hasattr(c, name):
            try:
                return getattr(c, name)(order_id=order_id, new_price=new_price)
            except TypeError:
                return getattr(c, name)(order_id, new_price)
    try:
        cancel_order(order_id)
    except Exception:
        pass
    return {"replaced": False, "cancelled": True}

def get_order_status(order_id: str) -> Dict[str, Any]:
    c = _client()
    for name in ("get_order", "order_status"):
        if hasattr(c, name):
            try:
                return getattr(c, name)(order_id)
            except Exception:
                pass
    return {"status": "unknown"}

def place_market_by_value(symbol: str, side: str, value_usdt: float) -> Dict[str, Any]:
    c = _client()
    side = "buy" if side.lower() == "long" else "sell"
    for name in ("open_market_by_value", "place_market_by_value", "close_reduce_market"):
        if hasattr(c, name):
            try:
                return getattr(c, name)(symbol=symbol, side=side, valueQty=value_usdt)
            except TypeError:
                return getattr(c, name)(symbol, side, value_usdt)
    raise RuntimeError("kucoin_adapter: market-by-value indisponible (optionnel)")
