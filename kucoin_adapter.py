# kucoin_adapter.py — robuste (ticker top, fix 330011 toggle, 300012 clamp, no crossMode)
import time, hmac, base64, hashlib, math
from typing import Any, Dict, Optional, Tuple

import httpx
import ujson as json

from config import SETTINGS
from logger_utils import get_logger

log = get_logger("kucoin.adapter")

BASE = SETTINGS.kucoin_base_url.rstrip("/")
ORDERS_PATH = "/api/v1/orders"
POS_PATH    = "/api/v1/position"
CNTR_PATH   = "/api/v1/contracts"
TIME_PATH   = "/api/v1/timestamp"
GET_BY_COID = "/api/v1/order/client-order/{clientOid}"
TICKER_PATH = "/api/v1/ticker"

_SERVER_OFFSET = 0.0

def _sync_server_time() -> None:
    global _SERVER_OFFSET
    try:
        with httpx.Client(timeout=5.0) as c:
            r = c.get(BASE + TIME_PATH)
            r.raise_for_status()
            server_ms = int((r.json() or {}).get("data", 0))
            _SERVER_OFFSET = (server_ms / 1000.0) - time.time()
            log.info(f"[time] offset={_SERVER_OFFSET:.3f}s")
    except Exception as e:
        log.warning(f"time sync failed: {e}")

def _ts_ms() -> int:
    return int((time.time() + _SERVER_OFFSET) * 1000)

def _b64_hmac_sha256(secret: str, payload: str) -> str:
    return base64.b64encode(hmac.new(secret.encode("utf-8"), payload.encode("utf-8"), hashlib.sha256).digest()).decode("utf-8")

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
                log.error(f"[POST {path}] HTTP={r.status_code} {r.text[:200]}")
            return ok, (data if isinstance(data, dict) else {})
    except Exception as e:
        log.error(f"[POST {path}] EXC={e}")
        return False, {"error": str(e)}

def _get(path: str, params: Optional[Dict[str, Any]] = None) -> Tuple[bool, Dict[str, Any]]:
    url = BASE + path
    hdrs = _headers("GET", path, "")
    try:
        with httpx.Client(timeout=10.0) as c:
            r = c.get(url, headers=hdrs, params=params)
            ok = (r.status_code == 200)
            data = r.json() if r.content else {}
            if not ok:
                log.error(f"[GET {path}] HTTP={r.status_code} {r.text[:200]}")
            return ok, (data if isinstance(data, dict) else {})
    except Exception as e:
        log.error(f"[GET {path}] EXC={e}")
        return False, {"error": str(e)}

# --------- Top of book (ticker only: depth1 peut 404 en Futures) ----------
def get_orderbook_top(symbol: str) -> Dict[str, Optional[float]]:
    ok, js = _get(TICKER_PATH, params={"symbol": symbol})
    if ok and isinstance(js, dict) and isinstance(js.get("data"), dict):
        d = js["data"]
        def _f(x):
            try: return float(x)
            except Exception: return None
        bb = _f(d.get("bestBidPrice") or d.get("buy") or d.get("bestBid"))
        ba = _f(d.get("bestAskPrice") or d.get("sell") or d.get("bestAsk"))
        bsz = _f(d.get("bestBidSize"))
        asz = _f(d.get("bestAskSize"))
        return {"bestBid": bb, "bestAsk": ba, "bidSize": bsz, "askSize": asz}
    return {"bestBid": None, "bestAsk": None, "bidSize": None, "askSize": None}

# --------- Métadonnées contrat / position ----------
def get_symbol_meta(symbol: str) -> Dict[str, Any]:
    ok, js = _get(f"{CNTR_PATH}/{symbol}")
    if ok:
        return js.get("data", {}) or {}
    return {}

def _safe_tick_from_meta(d: Dict[str, Any]) -> float:
    def _to_f(x) -> float:
        try: return float(x)
        except Exception: return 0.0
    tick = _to_f(d.get("tickSize")) or _to_f(d.get("priceIncrement"))
    if tick and tick > 0:
        return tick
    pp = d.get("pricePrecision")
    try:
        pp = int(pp)
        if pp is not None and pp >= 0:
            return 10 ** (-pp)
    except Exception:
        pass
    return 1e-8

def _price_increment(symbol: str) -> float:
    meta = get_symbol_meta(symbol) or {}
    t = _safe_tick_from_meta(meta)
    if t > 0:
        return t
    ok, js = _get(f"{CNTR_PATH}/active")
    if ok:
        for it in js.get("data", []) or []:
            if str(it.get("symbol", "")).strip().upper() == symbol.upper():
                return _safe_tick_from_meta(it)
    return 1e-8

def _quantize_price(price: float, tick: float, side: str) -> float:
    price = float(price); tick = float(tick)
    if tick <= 0: return price
    steps = price / tick
    qsteps = math.floor(steps + 1e-12) if str(side).lower() == "buy" else math.ceil(steps - 1e-12)
    return float(qsteps) * tick

def _margin_mode(symbol: str) -> Optional[bool]:
    ok, js = _get(f"{POS_PATH}?symbol={symbol}")
    if not ok: return None
    d = js.get("data") or {}
    try:
        cm = d.get("crossMode")
        return bool(cm) if cm is not None else None
    except Exception:
        return None

def _get_position_raw(symbol: str) -> Dict[str, Any]:
    ok, js = _get(f"{POS_PATH}?symbol={symbol}")
    if not ok or not isinstance(js, dict): return {}
    data = js.get("data")
    if isinstance(data, dict): return data
    if isinstance(data, list) and data: return data[0]
    return {}

def _infer_position_mode_from_payload(pos_json: Dict[str, Any]) -> str:
    if not pos_json: return "oneway"
    for k in ("positionMode", "posMode", "mode"):
        v = pos_json.get(k)
        if isinstance(v, str):
            v_low = v.lower()
            if "hedge" in v_low: return "hedge"
            if "one" in v_low or "single" in v_low: return "oneway"
    if isinstance(pos_json.get("isHedgeMode"), bool):
        return "hedge" if pos_json["isHedgeMode"] else "oneway"
    long_keys = ("longQty", "longSize", "longOpen", "longAvailable")
    short_keys = ("shortQty", "shortSize", "shortOpen", "shortAvailable")
    if any(k in pos_json for k in long_keys) and any(k in pos_json for k in short_keys):
        return "hedge"
    for k in ("positions", "items", "data"):
        arr = pos_json.get(k)
        if isinstance(arr, list) and len(arr) >= 2:
            sides = {str(x.get("side", "")).lower() for x in arr if isinstance(x, dict)}
            if "long" in sides and "short" in sides:
                return "hedge"
    return "oneway"

def _needs_position_side(position_mode: str) -> bool:
    return str(position_mode).lower() == "hedge"

def get_order_by_client_oid(client_oid: str) -> Optional[Dict[str, Any]]:
    ok, js = _get(GET_BY_COID.format(clientOid=client_oid))
    if not ok: return None
    return js.get("data") or None

# ---------- Prix passif pour postOnly ----------
def _clamp_postonly_price(symbol: str, side: str, raw_px: float, tick: float) -> float:
    quotes = get_orderbook_top(symbol)
    bb, ba = quotes.get("bestBid"), quotes.get("bestAsk")
    px = float(raw_px)
    if bb is None and ba is None:
        return _quantize_price(px, tick, side)
    s = str(side).lower()
    if s == "buy":
        anchor = bb if bb is not None else ba
        if anchor is not None:
            px = min(px, anchor - tick)
    else:
        anchor = ba if ba is not None else bb
        if anchor is not None:
            px = max(px, anchor + tick)
    return _quantize_price(px, tick, side)

# ---------- Place LIMIT ----------
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
    cross_mode: Optional[bool] = None,
) -> Dict[str, Any]:
    _sync_server_time()

    lev = int(leverage or getattr(SETTINGS, "default_leverage", 5))
    value_qty = float(value_usdt) * float(lev)

    tick = _price_increment(symbol)
    px = _quantize_price(float(price), tick, side)
    if post_only:
        px = _clamp_postonly_price(symbol, side, px, tick)

    if cross_mode is None:
        cm = _margin_mode(symbol); cross_mode = cm if cm is not None else None
    if cross_mode is not None:
        log.info("[marginMode] %s -> %s", symbol, ("cross" if cross_mode else "isolated"))

    pos_raw = _get_position_raw(symbol)
    pos_mode = _infer_position_mode_from_payload(pos_raw)
    include_ps = _needs_position_side(pos_mode)
    log.info("[positionMode] %s -> %s (include positionSide=%s)", symbol, pos_mode, include_ps)

    coid = client_order_id or str(_ts_ms())
    s_low = str(side).lower()

    def _make_body(lev_force: Optional[int] = None, include_position_side: bool = include_ps, price_override: Optional[float] = None) -> Dict[str, Any]:
        use_px = float(price_override) if (price_override is not None) else float(px)
        body = {
            "clientOid": coid,
            "symbol": symbol,
            "side": s_low,
            "type": "limit",
            "price": f"{use_px:.12f}",
            "valueQty": f"{value_qty:.4f}",
            "timeInForce": "GTC",
            "postOnly": bool(post_only),
            "leverage": str(lev_force if lev_force is not None else lev),
        }
        if include_position_side:
            body["positionSide"] = "long" if s_low == "buy" else "short"
        else:
            body.pop("positionSide", None)
        return body

    def _send(body: Dict[str, Any]) -> Dict[str, Any]:
        log.info("[place_limit] %s %s px=%s valueQty=%.2f postOnly=%s%s",
                 symbol, body.get("side"), body.get("price"), float(value_qty), body.get("postOnly"),
                 f" positionSide={body.get('positionSide')}" if "positionSide" in body else "")
        ok_http, js = _post(ORDERS_PATH, body)
        data = js.get("data") if isinstance(js, dict) else None
        order_id = data.get("orderId") if isinstance(data, dict) else None
        code = (js.get("code") or ""); msg = js.get("msg") or ""
        api_ok = bool(ok_http and code == "200000" and order_id)
        res = {"ok": api_ok, "code": code, "msg": msg, "orderId": order_id, "clientOid": body.get("clientOid"), "data": (data or {})}
        if not res["ok"]:
            log.info("[kc.place_limit_order] ok=%s code=%s msg=%s clientOid=%s orderId=%s",
                     res["ok"], res["code"], res["msg"], res["clientOid"], res["orderId"])
        return res

    # 1) essai initial (selon mode détecté)
    body = _make_body()
    resp = _send(body)

    # 2) leverage invalid
    if (not resp["ok"]) and ("Leverage parameter invalid" in str(resp.get("msg","")) or resp.get("code") == "100001"):
        lev_fb = int(getattr(SETTINGS, "default_leverage", 5) or 5)
        if lev_fb == lev: lev_fb = 5 if lev != 5 else 3
        log.info("[leverage] retry %s with leverage=%s", symbol, lev_fb)
        resp_fb = _send(_make_body(lev_force=lev_fb, include_position_side=include_ps))
        if resp_fb["ok"]: return resp_fb
        resp = resp_fb

    # 3) position mode mismatch → toggle explicite (test both)
    if (not resp["ok"]) and (resp.get("code") == "330011"):
        # a) inverse par rapport à include_ps
        alt = not include_ps
        log.info("[positionMode] toggle retry %s with include positionSide=%s", symbol, alt)
        resp2 = _send(_make_body(lev_force=None, include_position_side=alt))
        if resp2["ok"]: return resp2

        # b) si encore KO, retente avec include_ps original mais positionSide explicite long/short (déjà le cas)
        #    rien à faire ici; on garde resp2 comme dernière réponse
        resp = resp2

    # 4) prix hors bande → clamp via carnet et retry 1x
    if (not resp["ok"]) and (resp.get("code") == "300012"):
        px_retry = _clamp_postonly_price(symbol, side, float(price), tick)
        log.info("[price] retry %s with passive px=%s (clamped)", symbol, f"{px_retry:.12f}")
        resp3 = _send(_make_body(price_override=px_retry))
        return resp3

    return resp
