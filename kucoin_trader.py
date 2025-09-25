# -*- coding: utf-8 -*-
"""
kucoin_trader.py — LIMIT simple (one-way), valueQty USDT
- Jamais de `positionSide`
- N'ENVOIE PAS `crossMode`
- Pré-check du mode serveur (si Hedge -> refus local explicite)
- Tick robuste + mapping ...USDT -> ...USDTM
"""

import time, hmac, base64, hashlib, json, math
from typing import Optional, Dict, Any, Literal, List

import httpx
from config import SETTINGS
from logger_utils import get_logger

log = get_logger("kucoin.trader")

BASE = SETTINGS.kucoin_base_url.rstrip("/")
DEFAULT_LEVERAGE = int(getattr(SETTINGS, "default_leverage", 10))
VALUE_DECIMALS   = int(getattr(SETTINGS, "value_decimals", 2))

# -------- utils --------
def _to_api_symbol(symbol: str) -> str:
    s = str(symbol).upper().replace("/", "").replace("-", "")
    return s if s.endswith("USDTM") else (s + "M" if s.endswith("USDT") else s)

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
            d = (r.json() or {}).get("data", {}) or {}
            if "priceIncrement" not in d:
                tick = d.get("tickSize")
                if tick and float(tick) > 0:
                    d["priceIncrement"] = float(tick)
                else:
                    try:
                        pp = int(d.get("pricePrecision", 8))
                    except Exception:
                        pp = 8
                    d["priceIncrement"] = 10 ** (-pp) if pp >= 0 else 1e-8
            return d
    except Exception:
        pass
    return {"priceIncrement": 1e-8}

def _safe_tick(meta: Dict[str, Any]) -> float:
    try:
        t = float(meta.get("tickSize") or meta.get("priceIncrement") or 0.0)
        if t > 0: return t
    except Exception:
        pass
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

# -------- server mode check --------
def _infer_pos_mode(d: Dict[str, Any]) -> str:
    for k in ("positionMode","posMode","mode"):
        v = d.get(k)
        if isinstance(v, str):
            vl = v.lower()
            if "hedge" in vl: return "hedge"
            if "one" in vl or "single" in vl: return "oneway"
    long_keys=("longQty","longSize","longOpen","longAvailable")
    short_keys=("shortQty","shortSize","shortOpen","shortAvailable")
    if any(k in d for k in long_keys) and any(k in d for k in short_keys):
        return "hedge"
    return "oneway"

def _get_position_mode(c: httpx.Client, symbol_api: str) -> str:
    path = f"/api/v1/position?symbol={symbol_api}"
    try:
        r = c.get(BASE + path, headers=_headers("GET", path))
        if r.status_code == 200:
            data = (r.json() or {}).get("data")
            if isinstance(data, dict):
                m = _infer_pos_mode(data)
            elif isinstance(data, list) and data:
                m = _infer_pos_mode(data[0])
            else:
                m = "oneway"
        else:
            m = "oneway"
    except Exception:
        m = "oneway"
    log.info("[server positionMode] %s -> %s", symbol_api, m)
    return m

# -------- public API used by SFI --------
def get_symbol_meta(symbol: str) -> Dict[str, Any]:
    sym = _to_api_symbol(symbol)
    with httpx.Client(timeout=10.0) as c:
        return _contract_meta(c, sym)

def get_orderbook_top(symbol: str) -> Dict[str, Any] | None:
    try:
        sym = _to_api_symbol(symbol)
        with httpx.Client(timeout=10.0) as c:
            path = f"/api/v1/ticker?symbol={sym}"
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

def get_orderbook_levels(symbol: str, depth: int = 5) -> List[Dict[str, Any]]:
    try:
        sym = _to_api_symbol(symbol)
        with httpx.Client(timeout=10.0) as c:
            depth = min(20, max(5, int(depth)))
            path = f"/api/v1/level2/depth{depth}?symbol={sym}"
            r = c.get(BASE + path, headers=_headers("GET", path))
            if r.status_code != 200: return []
            data = (r.json() or {}).get("data") or {}
            out: List[Dict[str, Any]] = []
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
    value_usdt: float = 20.0,
    sl: Optional[float] = None,
    tp1: Optional[float] = None,
    tp2: Optional[float] = None,
    post_only: bool = True,
    client_order_id: Optional[str] = None,
    leverage: Optional[int] = None,
) -> Dict[str, Any]:
    sym = _to_api_symbol(symbol)
    with httpx.Client(timeout=10.0) as c:
        _sync_server_time(c)

        # ✅ Pré-check du mode serveur
        mode = _get_position_mode(c, sym)
        if mode == "hedge":
            msg = "Server account is in HEDGE mode — switch to One-Way on this API/sub-account."
            log.error("[precheck] %s", msg, extra={"symbol": sym})
            return {"ok": False, "code": "330011_LOCAL", "msg": msg, "clientOid": str(client_order_id or _now_ms())}

        meta = _contract_meta(c, sym)
        tick = _safe_tick(meta)
        qprice = _quantize(float(price), tick, side)

        body = {
            "clientOid": str(client_order_id or _now_ms()),
            "symbol": sym,
            "type": "limit",
            "side": side.lower(),
            "price": f"{qprice:.12f}",
            "valueQty": f"{float(value_usdt):.{VALUE_DECIMALS}f}",
            "leverage": str(int(leverage or DEFAULT_LEVERAGE)),
            "timeInForce": "GTC",
            "postOnly": bool(post_only),
            "reduceOnly": False,
            # ❌ pas de crossMode
            # ❌ jamais positionSide
        }

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

        # Retry levier uniquement
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

        if "ok" not in js:
            js["ok"] = bool(ok)
        if "orderId" not in js and isinstance(js.get("data"), dict):
            js["orderId"] = js["data"].get("orderId")
        js.setdefault("clientOid", body.get("clientOid"))
        js.setdefault("price_sent", qprice)
        js.setdefault("tick", tick)
        js.setdefault("symbol_api", sym)
        return js

def place_limit_valueqty(
    symbol: str, side: str, price: float, value_usdt: float,
    sl: Optional[float] = None, tp1: Optional[float] = None, tp2: Optional[float] = None,
    post_only: bool = True, client_order_id: Optional[str] = None, extra_kwargs: Optional[Dict[str, Any]] = None
) -> Dict[str, Any]:
    return place_limit_order(symbol, side, price, value_usdt, sl, tp1, tp2, post_only, client_order_id)

def cancel_order(order_id: str) -> Dict[str, Any]:
    with httpx.Client(timeout=10.0) as c:
        path = f"/api/v1/orders/{order_id}/cancel"
        r = c.post(BASE + path, headers=_headers("POST", path, ""))
        try:
            js = r.json() if (r.content and r.text) else {}
        except Exception:
            js = {}
        js["ok"] = bool(r.status_code == 200 and (js or {}).get("code") == "200000")
        return js

def replace_order(order_id: str, new_price: float) -> Dict[str, Any]:
    try:
        c = cancel_order(order_id)
        return {"ok": bool(c.get("ok")), "cancel": c}
    except Exception as e:
        return {"ok": False, "error": str(e)}

def place_market_by_value(symbol: str, side: Literal["buy","sell"], valueQty: float) -> Dict[str, Any]:
    sym = _to_api_symbol(symbol)
    with httpx.Client(timeout=10.0) as c:
        _sync_server_time(c)

        # Pré-check du mode
        mode = _get_position_mode(c, sym)
        if mode == "hedge":
            msg = "Server account is in HEDGE mode — switch to One-Way on this API/sub-account."
            log.error("[precheck] %s", msg, extra={"symbol": sym})
            return {"ok": False, "code": "330011_LOCAL", "msg": msg, "clientOid": str(_now_ms())}

        body = {
            "clientOid": str(_now_ms()),
            "symbol": sym,
            "type": "market",
            "side": side.lower(),
            "valueQty": f"{float(valueQty):.{VALUE_DECIMALS}f}",
            "reduceOnly": False,
            # ❌ pas de crossMode / positionSide
        }
        path = "/api/v1/orders"
        body_json = json.dumps(body, separators=(",", ":"))
        r = c.post(BASE + path, headers=_headers("POST", path, body_json), content=body_json)
        try:
            js = r.json() if (r.content and r.text) else {}
        except Exception:
            js = {}
        js["ok"] = bool(r.status_code == 200 and (js or {}).get("code") == "200000")
        if "orderId" not in js and isinstance(js.get("data"), dict):
            js["orderId"] = js["data"].get("orderId")
        js.setdefault("symbol_api", sym)
        return js
