# kucoin_adapter.py — Adapter KuCoin Futures (REST v2 signé) prêt à coller
import time
import hmac
import base64
import hashlib
import httpx
import json
from decimal import Decimal, ROUND_DOWN, getcontext
from typing import Any, Dict, Optional

from config import SETTINGS
from logger_utils import get_logger

log = get_logger("kucoin.adapter")

BASE = SETTINGS.kucoin_base_url.rstrip("/")
ORDERS_PATH = "/api/v1/orders"
TIMESTAMP_PATH = "/api/v1/timestamp"
CONTRACT_PATH = "/api/v1/contracts/{symbol}"
CLIENT_QUERY_PATH = "/api/v1/order/client-order/{clientOid}"
CLIENT_CANCEL_PATH = "/api/v1/order/cancelClientOrder?clientOid={clientOid}"

getcontext().prec = 28
_SERVER_OFFSET = 0.0

def _sync_server_time() -> None:
    global _SERVER_OFFSET
    try:
        r = httpx.get(BASE + TIMESTAMP_PATH, timeout=5.0)
        r.raise_for_status()
        server_ms = int(r.json().get("data", 0))
        _SERVER_OFFSET = (server_ms / 1000.0) - time.time()
        log.info(f"[time] offset={_SERVER_OFFSET:.3f}s")
    except Exception as e:
        log.warning(f"[time] sync failed: {e}")

def _ts_ms() -> int:
    return int((time.time() + _SERVER_OFFSET) * 1000)

def _b64_hmac_sha256(secret: str, payload: str) -> str:
    return base64.b64encode(
        hmac.new(secret.encode("utf-8"), payload.encode("utf-8"), hashlib.sha256).digest()
    ).decode("utf-8")

def _headers(method: str, path: str, body_str: str = "") -> Dict[str, str]:
    ts = str(_ts_ms())
    sig_payload = ts + method.upper() + path + (body_str or "")
    return {
        "KC-API-KEY": SETTINGS.kucoin_key,
        "KC-API-SIGN": _b64_hmac_sha256(SETTINGS.kucoin_secret, sig_payload),
        "KC-API-TIMESTAMP": ts,
        "KC-API-PASSPHRASE": _b64_hmac_sha256(SETTINGS.kucoin_secret, SETTINGS.kucoin_passphrase),
        "KC-API-KEY-VERSION": "2",
        "Accept": "application/json",
        "Content-Type": "application/json",
    }

def _post(path: str, body: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    body_str = "" if body is None else json.dumps(body, separators=(",", ":"), ensure_ascii=False)
    try:
        with httpx.Client(timeout=10.0) as c:
            r = c.post(BASE + path, headers=_headers("POST", path, body_str),
                       content=(body_str.encode("utf-8") if body_str else None))
            js = r.json() if r.content else {}
            if r.status_code not in (200, 201):
                log.error(f"[POST {path}] HTTP={r.status_code} {r.text[:200]}")
            return js
    except Exception as e:
        log.exception(f"[POST {path}] exception: {e}")
        return {"code": "EXC", "msg": str(e), "data": {}}

def _get(path: str) -> Dict[str, Any]:
    try:
        with httpx.Client(timeout=10.0) as c:
            r = c.get(BASE + path, headers=_headers("GET", path))
            js = r.json() if r.content else {}
            if r.status_code != 200:
                log.error(f"[GET {path}] HTTP={r.status_code} {r.text[:200]}")
            return js
    except Exception as e:
        log.exception(f"[GET {path}] exception: {e}")
        return {"code": "EXC", "msg": str(e), "data": {}}

def get_symbol_meta(symbol: str) -> Dict[str, Any]:
    sym = symbol.upper().strip()
    if not sym.endswith("USDTM") and sym.endswith("USDT"):
        sym = sym + "M"
    js = _get(CONTRACT_PATH.format(symbol=sym))
    data = js.get("data", {}) if isinstance(js, dict) else {}
    tick = None
    for key in ("priceIncrement", "tickSize"):
        v = data.get(key)
        if v is None: 
            continue
        try:
            tick = Decimal(str(v)); break
        except Exception:
            continue
    if not tick or tick <= 0:
        tick = Decimal("0.001")
    tick_q = Decimal(str(tick.normalize()))
    return {
        "symbol_api": sym,
        "priceIncrement": tick_q,
        "pricePrecision": max(0, -tick_q.as_tuple().exponent),
        "raw": data,
    }

def _quantize_to_tick(price: float, tick: Decimal) -> Decimal:
    p = Decimal(str(price))
    if tick <= 0:
        return p
    mult = p / tick
    mult_q = mult.quantize(Decimal("1"), rounding=ROUND_DOWN)
    return mult_q * tick

def _normalize_side(side: str) -> str:
    s = (side or "").lower().strip()
    if s in ("buy", "sell"):
        return s
    if s == "long":
        return "buy"
    if s == "short":
        return "sell"
    # default: assume engine gave us 'buy'/'sell'
    return s or "buy"

def place_limit_order(
    symbol: str,
    side: str,
    price: float,
    value_usdt: float,
    sl: Optional[float] = None,
    tp1: Optional[float] = None,
    tp2: Optional[float] = None,
    post_only: bool = True,
    client_order_id: Optional[str] = None,
    leverage: Optional[int] = None,
    **kwargs,
) -> Dict[str, Any]:
    _sync_server_time()
    meta = get_symbol_meta(symbol)
    tick: Decimal = meta.get("priceIncrement", Decimal("0.001"))
    prec = meta.get("pricePrecision", 3)
    sym_api = meta.get("symbol_api", symbol if symbol.endswith("USDTM") else symbol + "M")

    q_price = _quantize_to_tick(price, tick)
    price_str = f"{q_price:.{prec}f}"
    side_norm = _normalize_side(side)

    lev = str(leverage if leverage else getattr(SETTINGS, "default_leverage", 10))
    value_qty = float(value_usdt) * float(lev)

    body = {
        "clientOid": client_order_id or str(_ts_ms()),
        "symbol": sym_api,
        "type": "limit",
        "side": side_norm,
        "price": price_str,
        "valueQty": f"{value_qty:.2f}",
        "leverage": lev,
        "timeInForce": "GTC",
        "reduceOnly": False,
        "postOnly": bool(post_only),
    }

    log.info(f"[place_limit] {sym_api} {body['side']} px={price_str} valueQty={body['valueQty']} postOnly={body['postOnly']}")
    js = _post(ORDERS_PATH, body)

    ok_resp = bool(js.get("code") in ("200000", 200000) or js.get("success") is True)
    code = str(js.get("code"))
    msg = js.get("msg") or js.get("message") or ""
    order_id = None
    client_oid = body["clientOid"]

    if not ok_resp and ("Price parameter invalid" in msg or "multiple" in msg.lower()):
        # retry sans postOnly si rejet maker
        log.info("[place_limit] retry tick (postOnly=True->False)")
        body["postOnly"] = False
        js2 = _post(ORDERS_PATH, body)
        ok_resp = bool(js2.get("code") in ("200000", 200000) or js2.get("success") is True)
        code = str(js2.get("code"))
        msg = js2.get("msg") or js2.get("message") or msg
        if ok_resp:
            d = js2.get("data") or {}
            order_id = d.get("orderId")
            return {"ok": True, "code": code, "msg": "", "orderId": order_id, "clientOid": client_oid, "raw": js2}
        return {"ok": False, "code": code, "msg": msg, "orderId": None, "clientOid": None, "raw": js2}

    d = js.get("data") or {}
    order_id = d.get("orderId")
    return {"ok": ok_resp, "code": code, "msg": ("" if ok_resp else msg), "orderId": order_id, "clientOid": client_oid, "raw": js}

def get_order_by_client_oid(client_oid: str) -> Dict[str, Any]:
    if not client_oid:
        return {"ok": False, "code": "PARAM", "msg": "clientOid missing", "data": {}}
    _sync_server_time()
    js = _get(CLIENT_QUERY_PATH.format(clientOid=client_oid))
    ok = bool(js.get("code") in ("200000", 200000))
    return {"ok": ok, "code": str(js.get("code")), "msg": js.get("msg", ""), "data": js.get("data", {})}

def cancel_by_client_oid(client_oid: str) -> Dict[str, Any]:
    if not client_oid:
        return {"ok": False, "code": "PARAM", "msg": "clientOid missing", "data": {}}
    _sync_server_time()
    js = _get(CLIENT_CANCEL_PATH.format(clientOid=client_oid))
    ok = bool(js.get("code") in ("200000", 200000))
    return {"ok": ok, "code": str(js.get("code")), "msg": js.get("msg", ""), "data": js.get("data", {})}
