# -*- coding: utf-8 -*-
"""
kucoin_trader.py — LIMIT valueQty 20 USDT, iso, levier 10
- Auto hedge/one-way: ajoute positionSide si hedge détecté
- Retry 100001 (leverage), 330011 (flip positionSide)
- Tick robuste
"""

import time, hmac, base64, hashlib, json, math
from typing import Optional, Tuple, Dict, Any, Literal

import httpx
from config import SETTINGS
from logger_utils import get_logger

log = get_logger("kucoin.trader")

BASE = SETTINGS.kucoin_base_url.rstrip("/")
DEFAULT_LEVERAGE = int(getattr(SETTINGS, "default_leverage", 10))
VALUE_USDT       = float(getattr(SETTINGS, "margin_per_trade", 20.0))
VALUE_DECIMALS   = int(getattr(SETTINGS, "value_decimals", 2))

# -------- time sync --------
_SERVER_OFFSET = 0.0
def _sync_server_time(client: httpx.Client):
    global _SERVER_OFFSET
    try:
        r = client.get(BASE + "/api/v1/timestamp", timeout=5.0)
        if r.status_code == 200:
            server_ms = int((r.json() or {}).get("data", 0))
            _SERVER_OFFSET = (server_ms / 1000.0) - time.time()
            log.info("time offset=%.3fs", _SERVER_OFFSET)
    except Exception as e:
        log.warning("time sync failed: %s", e)

def _now_ms() -> int:
    return int((time.time() + _SERVER_OFFSET) * 1000)

def _b64_hmac_sha256(secret: str, payload: str) -> str:
    return base64.b64encode(
        hmac.new(secret.encode("utf-8"), payload.encode("utf-8"), hashlib.sha256).digest()
    ).decode()

def _headers(method: str, path: str, body: str = "") -> Dict[str, str]:
    ts = str(_now_ms())
    sig = _b64_hmac_sha256(SETTINGS.kucoin_secret, ts + method + path + body)
    psp = _b64_hmac_sha256(SETTINGS.kucoin_secret, SETTINGS.kucoin_passphrase)
    return {
        "KC-API-KEY": SETTINGS.kucoin_key,
        "KC-API-SIGN": sig,
        "KC-API-TIMESTAMP": ts,
        "KC-API-PASSPHRASE": psp,
        "KC-API-KEY-VERSION": "2",
        "Content-Type": "application/json",
        "Accept": "application/json",
    }

# -------- meta / tick --------
def _contract_meta(c: httpx.Client, symbol_api: str) -> Dict[str, Any]:
    try:
        path = f"/api/v1/contracts/{symbol_api}"
        r = c.get(BASE + path, headers=_headers("GET", path))
        if r.status_code == 200:
            return (r.json() or {}).get("data", {}) or {}
    except Exception:
        pass
    return {}

def _safe_tick(meta: Dict[str, Any]) -> float:
    def _f(x):
        try: return float(x)
        except Exception: return 0.0
    t = _f(meta.get("tickSize") or meta.get("priceIncrement"))
    if t > 0: return t
    try:
        pp = int(meta.get("pricePrecision", 8))
        return 10 ** (-pp) if pp >= 0 else 1e-8
    except Exception:
        return 1e-8

def _quantize(price: float, tick: float, side: str) -> float:
    if tick <= 0: return float(price)
    steps = float(price) / float(tick)
    qsteps = math.floor(steps + 1e-12) if side.lower() == "buy" else math.ceil(steps - 1e-12)
    return float(qsteps) * float(tick)

# -------- position mode --------
def _get_position(c: httpx.Client, symbol_api: str) -> Dict[str, Any]:
    try:
        path = f"/api/v1/position?symbol={symbol_api}"
        r = c.get(BASE + path, headers=_headers("GET", path))
        if r.status_code == 200:
            data = (r.json() or {}).get("data")
            if isinstance(data, dict): return data
            if isinstance(data, list) and data: return data[0]
    except Exception:
        pass
    return {}

def _infer_pos_mode(pos: Dict[str, Any]) -> str:
    for k in ("positionMode","posMode","mode"):
        v = pos.get(k)
        if isinstance(v, str):
            vl = v.lower()
            if "hedge" in vl: return "hedge"
            if "one" in vl or "single" in vl: return "oneway"
    long_keys=("longQty","longSize","longOpen","longAvailable")
    short_keys=("shortQty","shortSize","shortOpen","shortAvailable")
    if any(k in pos for k in long_keys) and any(k in pos for k in short_keys):
        return "hedge"
    return "oneway"

# -------- public API used by SFI --------
def get_symbol_meta(symbol: str) -> Dict[str, Any]:
    with httpx.Client(timeout=10.0) as c:
        return _contract_meta(c, symbol)

def get_orderbook_top(symbol: str) -> Dict[str, Any] | None:
    try:
        with httpx.Client(timeout=10.0) as c:
            path = f"/api/v1/ticker?symbol={symbol}"
            r = c.get(BASE + path, headers=_headers("GET", path))
            if r.status_code != 200: return None
            d = (r.json() or {}).get("data") or {}
            return {
                "bestBid": float(d.get("bestBidPrice")) if d.get("bestBidPrice") else None,
                "bestAsk": float(d.get("bestAskPrice")) if d.get("bestAskPrice") else None,
                "bidSize": float(d.get("bestBidSize")) if d.get("bestBidSize") else None,
                "askSize": float(d.get("bestAskSize")) if d.get("bestAskSize") else None,
            }
    except Exception:
        return None

def get_orderbook_levels(symbol: str, depth: int = 5) -> list[Dict[str, Any]]:
    try:
        with httpx.Client(timeout=10.0) as c:
            depth = min(20, max(5, int(depth)))
            path = f"/api/v1/level2/depth{depth}?symbol={symbol}"
            r = c.get(BASE + path, headers=_headers("GET", path))
            if r.status_code != 200: return []
            data = (r.json() or {}).get("data") or {}
            out = []
            for p, sz in (data.get("bids") or []):
                out.append({"side": "buy", "price": float(p), "size": float(sz)})
            for p, sz in (data.get("asks") or []):
                out.append({"side": "sell", "price": float(p), "size": float(sz)})
            return out
    except Exception:
        return []

def get_order_status(order_id: str) -> Dict[str, Any]:
    with httpx.Client(timeout=10.0) as c:
        path = f"/api/v1/orders/{order_id}"
        r = c.get(BASE + path, headers=_headers("GET", path))
        return (r.json() or {}).get("data") or {}

def get_order_by_client_oid(client_oid: str) -> Optional[Dict[str, Any]]:
    with httpx.Client(timeout=10.0) as c:
        path = f"/api/v1/order/client-order/{client_oid}"
        r = c.get(BASE + path, headers=_headers("GET", path))
        if r.status_code != 200: return None
        return (r.json() or {}).get("data") or None

def place_limit_order(
    symbol: str,
    side: Literal["buy","sell"],
    price: float,
    value_usdt: float = VALUE_USDT,
    sl: Optional[float] = None,
    tp1: Optional[float] = None,
    tp2: Optional[float] = None,
    post_only: bool = True,
    client_order_id: Optional[str] = None,
    leverage: Optional[int] = None,
) -> Dict[str, Any]:
    with httpx.Client(timeout=10.0) as c:
        _sync_server_time(c)
        meta = _contract_meta(c, symbol)
        tick = _safe_tick(meta)
        qprice = _quantize(float(price), tick, side)

        pos = _get_position(c, symbol)
        mode = _infer_pos_mode(pos)
        include_ps = (mode == "hedge")
        log.info("[positionMode] %s -> %s (include positionSide=%s)", symbol, mode, include_ps)

        body = {
            "clientOid": str(client_order_id or _now_ms()),
            "symbol": symbol,
            "type": "limit",
            "side": side.lower(),
            "price": f"{qprice:.12f}",
            "valueQty": f"{float(value_usdt):.{VALUE_DECIMALS}f}",
            "leverage": str(int(leverage or DEFAULT_LEVERAGE)),
            "timeInForce": "GTC",
            "postOnly": bool(post_only),
            "reduceOnly": False,
        }
        if include_ps:
            body["positionSide"] = "long" if side.lower() == "buy" else "short"

        path = "/api/v1/orders"
        body_json = json.dumps(body, separators=(",", ":"))
        r = c.post(BASE + path, headers=_headers("POST", path, body_json), content=body_json)
        try:
            js = r.json() if (r.content and r.text) else {}
        except Exception:
            js = {}
        code = (js or {}).get("code")
        msg  = (js or {}).get("msg")
        ok = (r.status_code == 200 and code == "200000")

        # Retry leverage
        if (not ok) and (code == "100001" or (msg and "Leverage parameter invalid" in str(msg))):
            body["leverage"] = "5" if str(body.get("leverage")) != "5" else "3"
            body_json = json.dumps(body, separators=(",", ":"))
            r = c.post(BASE + path, headers=_headers("POST", path, body_json), content=body_json)
            try:
                js = r.json() if (r.content and r.text) else {}
            except Exception:
                js = {}
            code = (js or {}).get("code")
            ok = (r.status_code == 200 and code == "200000")

        # Retry 330011 (flip positionSide inclusion)
        if (not ok) and code == "330011":
            alt_include_ps = not include_ps
            log.info("[positionMode] retry %s with positionSide included=%s", symbol, alt_include_ps)
            if alt_include_ps:
                body["positionSide"] = "long" if side.lower() == "buy" else "short"
            else:
                body.pop("positionSide", None)
            body_json = json.dumps(body, separators=(",", ":"))
            r = c.post(BASE + path, headers=_headers("POST", path, body_json), content=body_json)
            try:
                js = r.json() if (r.content and r.text) else {}
            except Exception:
                js = {}
            code = (js or {}).get("code")
            ok = (r.status_code == 200 and code == "200000")

        if "ok" not in js:
            js["ok"] = bool(ok)
        if "orderId" not in js and isinstance(js.get("data"), dict):
            js["orderId"] = js["data"].get("orderId")
        js.setdefault("clientOid", body.get("clientOid"))
        js.setdefault("price_sent", qprice)
        js.setdefault("tick", tick)
        return js

def place_limit_valueqty(
    symbol: str, side: str, price: float, value_usdt: float,
    sl: Optional[float] = None, tp1: Optional[float] = None, tp2: Optional[float] = None,
    post_only: bool = True, client_order_id: Optional[str] = None, extra_kwargs: Optional[Dict[str, Any]] = None
) -> Dict[str, Any]:
    return place_limit_order(symbol, side, price, value_usdt, sl, tp1, tp2, post_only, client_order_id)
