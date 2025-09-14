# -*- coding: utf-8 -*-
"""
kucoin_adapter.py — LIMIT simple (one-way), isolé, levier 10, valueQty (USDT)
- Jamais de `positionSide`
- Envoie `crossMode=False` (isolé)
- `leverage` par défaut = 10 (fallback 5→3 si 100001)
- `postOnly` True par défaut
- Quantification prix au tick (tickSize/priceIncrement -> pricePrecision)
"""

from __future__ import annotations
import time, math, hmac, base64, hashlib
from typing import Any, Dict, Optional, Tuple, List

import httpx
import ujson as json

from config import SETTINGS
from logger_utils import get_logger

log = get_logger("kucoin.adapter")

BASE = SETTINGS.kucoin_base_url.rstrip("/")
TIME_PATH   = "/api/v1/timestamp"
CNTR_PATH   = "/api/v1/contracts"
ORDERS_PATH = "/api/v1/orders"
GET_BY_COID = "/api/v1/order/client-order/{clientOid}"

DEFAULT_LEVERAGE = int(getattr(SETTINGS, "default_leverage", 10))
VALUE_DECIMALS   = int(getattr(SETTINGS, "value_decimals", 2))

# ------------ time sync ------------
_SERVER_OFFSET = 0.0
def _sync_server_time() -> None:
    global _SERVER_OFFSET
    try:
        with httpx.Client(timeout=5.0) as c:
            r = c.get(BASE + TIME_PATH)
            r.raise_for_status()
            server_ms = int((r.json() or {}).get("data", 0))
            _SERVER_OFFSET = (server_ms / 1000.0) - time.time()
            log.info("time offset=%.3fs", _SERVER_OFFSET)
    except Exception as e:
        log.warning("time sync failed: %s", e)

def _ts_ms() -> int:
    return int((time.time() + _SERVER_OFFSET) * 1000)

# ------------ v2 sign ------------
def _b64_hmac_sha256(secret: str, payload: str) -> str:
    return base64.b64encode(
        hmac.new(secret.encode("utf-8"), payload.encode("utf-8"), hashlib.sha256).digest()
    ).decode("utf-8")

def _headers(method: str, path: str, body_str: str = "") -> Dict[str, str]:
    ts = str(_ts_ms())
    sig = _b64_hmac_sha256(SETTINGS.kucoin_secret, ts + method.upper() + path + (body_str or ""))
    psp = _b64_hmac_sha256(SETTINGS.kucoin_secret, SETTINGS.kucoin_passphrase)
    return {
        "KC-API-KEY": SETTINGS.kucoin_key,
        "KC-API-SIGN": sig,
        "KC-API-TIMESTAMP": ts,
        "KC-API-PASSPHRASE": psp,
        "KC-API-KEY-VERSION": "2",
        "Accept": "application/json",
        "Content-Type": "application/json",
        "User-Agent": "bot/kucoin-adapter",
    }

# ------------ HTTP ------------
def _post(path: str, body: Optional[Dict[str, Any]]) -> Tuple[bool, Dict[str, Any]]:
    url = BASE + path
    body_str = "" if body is None else json.dumps(body, separators=(",", ":"), ensure_ascii=False)
    hdrs = _headers("POST", path, body_str)
    try:
        with httpx.Client(timeout=10.0) as c:
            r = c.post(url, headers=hdrs, content=(body_str.encode("utf-8") if body_str else None))
            ok = (r.status_code == 200)
            data = r.json() if r.content else {}
            if not ok:
                log.error("[POST %s] HTTP=%s %s", path, r.status_code, r.text[:200])
            return ok, (data if isinstance(data, dict) else {})
    except Exception as e:
        log.error("[POST %s] EXC=%s", path, e)
        return False, {"error": str(e)}

def _get(path: str) -> Tuple[bool, Dict[str, Any]]:
    url = BASE + path
    hdrs = _headers("GET", path, "")
    try:
        with httpx.Client(timeout=10.0) as c:
            r = c.get(url, headers=hdrs)
            ok = (r.status_code == 200)
            data = r.json() if r.content else {}
            if not ok:
                log.error("[GET %s] HTTP=%s %s", path, r.status_code, r.text[:200])
            return ok, (data if isinstance(data, dict) else {})
    except Exception as e:
        log.error("[GET %s] EXC=%s", path, e)
        return False, {"error": str(e)}

# ------------ Meta / Tick ------------
def get_symbol_meta(symbol: str) -> Dict[str, Any]:
    ok, js = _get(f"{CNTR_PATH}/{symbol}")
    d = (js.get("data") or {}) if ok else {}
    # expose priceIncrement pour SFI
    if "priceIncrement" not in d:
        tick = d.get("tickSize")
        if tick and float(tick) > 0:
            d["priceIncrement"] = float(tick)
        else:
            pp = int(d.get("pricePrecision", 8))
            d["priceIncrement"] = 10 ** (-pp) if pp >= 0 else 1e-8
    return d

def _safe_tick_from_meta(d: Dict[str, Any]) -> float:
    try:
        t = float(d.get("tickSize") or d.get("priceIncrement") or 0.0)
        if t > 0: return t
    except Exception:
        pass
    try:
        pp = int(d.get("pricePrecision", 8))
        return 10 ** (-pp) if pp >= 0 else 1e-8
    except Exception:
        return 1e-8

def _quantize(price: float, tick: float, side: str) -> float:
    if tick <= 0: return float(price)
    steps = float(price) / float(tick)
    if str(side).lower() == "buy":
        qsteps = math.floor(steps + 1e-12)  # floor
    else:
        qsteps = math.ceil(steps - 1e-12)   # ceil
    return float(qsteps) * float(tick)

# ------------ Orderbook (optionnels) ------------
def get_orderbook_top(symbol: str) -> Dict[str, Any] | None:
    try:
        ok, js = _get(f"/api/v1/ticker?symbol={symbol}")
        if ok:
            d = js.get("data") or {}
            return {
                "bestBid": float(d.get("bestBidPrice")) if d.get("bestBidPrice") else None,
                "bestAsk": float(d.get("bestAskPrice")) if d.get("bestAskPrice") else None,
                "bidSize": float(d.get("bestBidSize")) if d.get("bestBidSize") else None,
                "askSize": float(d.get("bestAskSize")) if d.get("bestAskSize") else None,
            }
    except Exception:
        pass
    return None

def get_orderbook_levels(symbol: str, depth: int = 5) -> List[Dict[str, Any]]:
    try:
        d = []
        ok, js = _get(f"/api/v1/level2/depth{min(20, max(5, depth))}?symbol={symbol}")
        if ok:
            data = js.get("data") or {}
            for p, sz in (data.get("bids") or []):
                d.append({"side": "buy", "price": float(p), "size": float(sz)})
            for p, sz in (data.get("asks") or []):
                d.append({"side": "sell", "price": float(p), "size": float(sz)})
        return d
    except Exception:
        return []

# ------------ Status / Lookup ------------
def get_order_status(order_id: str) -> Dict[str, Any]:
    ok, js = _get(f"/api/v1/orders/{order_id}")
    return (js.get("data") or {}) if ok else {"status": "unknown"}

def get_order_by_client_oid(client_oid: str) -> Optional[Dict[str, Any]]:
    ok, js = _get(GET_BY_COID.format(clientOid=client_oid))
    return (js.get("data") or None) if ok else None

# ------------ PLACE LIMIT (one-way strict) ------------
def place_limit_order(
    symbol: str,
    side: str,
    price: float,
    value_usdt: float = 20.0,
    sl: Optional[float] = None,
    tp1: Optional[float] = None,
    tp2: Optional[float] = None,
    post_only: bool = True,
    client_order_id: Optional[str] = None,
    leverage: Optional[int] = None,
) -> Dict[str, Any]:
    if _SERVER_OFFSET == 0.0 or abs(_SERVER_OFFSET) > 30:
        _sync_server_time()

    meta = get_symbol_meta(symbol) or {}
    tick = _safe_tick_from_meta(meta)
    qprice = _quantize(float(price), float(tick), side)

    lev = int(leverage or DEFAULT_LEVERAGE)
    coid = str(client_order_id or _ts_ms())

    body = {
        "clientOid": coid,
        "symbol": symbol,
        "type": "limit",
        "side": str(side).lower(),              # "buy"/"sell"
        "price": f"{qprice:.12f}",
        "valueQty": f"{float(value_usdt):.{VALUE_DECIMALS}f}",
        "leverage": str(lev),
        "timeInForce": "GTC",
        "postOnly": bool(post_only),
        "reduceOnly": False,
        "crossMode": False,                     # isolé
        # ❌ jamais positionSide
    }

    ok, js = _post(ORDERS_PATH, body)
    code = (js or {}).get("code")
    msg  = (js or {}).get("msg")

    # retry levier uniquement
    if (not ok or code != "200000") and (code == "100001" or (msg and "Leverage parameter invalid" in str(msg))):
        body["leverage"] = "5" if str(body.get("leverage")) != "5" else "3"
        ok, js = _post(ORDERS_PATH, body)

    js = js or {}
    js["ok"] = bool(ok and js.get("code") == "200000")
    if "orderId" not in js and isinstance(js.get("data"), dict):
        js["orderId"] = js["data"].get("orderId")
    js.setdefault("clientOid", coid)
    js.setdefault("price_sent", qprice)
    js.setdefault("tick", tick)
    return js

# alias legacy
def place_limit_valueqty(
    symbol: str, side: str, price: float, value_usdt: float,
    sl: Optional[float] = None, tp1: Optional[float] = None, tp2: Optional[float] = None,
    post_only: bool = True, client_order_id: Optional[str] = None, extra_kwargs: Optional[Dict[str, Any]] = None
) -> Dict[str, Any]:
    return place_limit_order(symbol, side, price, value_usdt, sl, tp1, tp2, post_only, client_order_id)

# ------------ Cancel / Replace ------------
def cancel_order(order_id: str) -> Dict[str, Any]:
    ok, js = _post(f"/api/v1/orders/{order_id}/cancel", None)
    return {"ok": ok and (js or {}).get("code") == "200000", **(js or {})}

def replace_order(order_id: str, new_price: float) -> Dict[str, Any]:
    # cancel-only; le caller re-postera au nouveau prix
    try:
        c = cancel_order(order_id)
        return {"ok": bool(c.get("ok")), "cancel": c}
    except Exception as e:
        return {"ok": False, "error": str(e)}

# ------------ Market-by-value (optionnel) ------------
def place_market_by_value(symbol: str, side: str, valueQty: float) -> Dict[str, Any]:
    body = {
        "clientOid": str(_ts_ms()),
        "symbol": symbol,
        "type": "market",
        "side": str(side).lower(),
        "valueQty": f"{float(valueQty):.{VALUE_DECIMALS}f}",
        "reduceOnly": False,
        "crossMode": False,
    }
    ok, js = _post(ORDERS_PATH, body)
    js = js or {}
    js["ok"] = bool(ok and js.get("code") == "200000")
    if "orderId" not in js and isinstance(js.get("data"), dict):
        js["orderId"] = js["data"].get("orderId")
    return js
