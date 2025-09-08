# kucoin_adapter.py — robuste (no positionSide, positionId fallback, ticker top, 330011/300012/100001 fixes)
# + hooks SFI: get_orderbook_top, place_market_by_value, cancel_order, get_order_status

import re
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

# --- options (env / settings)
ALLOW_TAKER_ON_BAND_RETRY = bool(int(getattr(SETTINGS, "kc_allow_taker_on_band_retry", 1)))  # coupe postOnly au retry 300012
DEFAULT_LEVERAGE = int(getattr(SETTINGS, "default_leverage", 5))

_SERVER_OFFSET = 0.0

# ---------------- time & auth ----------------
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

# ---------------- http helpers ----------------
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

def _delete(path: str) -> Tuple[bool, Dict[str, Any]]:
    url = BASE + path
    hdrs = _headers("DELETE", path, "")
    try:
        with httpx.Client(timeout=10.0) as c:
            r = c.delete(url, headers=hdrs)
            ok = (r.status_code == 200)
            data = r.json() if r.content else {}
            if not ok:
                log.error(f"[DELETE {path}] HTTP={r.status_code} {r.text[:200]}")
            return ok, (data if isinstance(data, dict) else {})
    except Exception as e:
        log.error(f"[DELETE {path}] EXC={e}")
        return False, {"error": str(e)}

# --------- Ticker top (depth1 peut 404) ----------
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

# --------- Métadonnées / tick ----------
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

# --------- Position / hedge helpers ----------
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

def _extract_position_ids(pos_json: Dict[str, Any]) -> Tuple[Optional[str], Optional[str]]:
    candidates = [
        ("longPositionId", "shortPositionId"),
        ("longId", "shortId"),
        ("positionIdLong", "positionIdShort"),
        ("longPosId", "shortPosId"),
    ]
    for lkey, skey in candidates:
        lid = pos_json.get(lkey); sid = pos_json.get(skey)
        if lid or sid:
            return (str(lid) if lid else None, str(sid) if sid else None)
    items = pos_json.get("positions") or pos_json.get("items") or pos_json.get("data")
    if isinstance(items, list):
        l_id = s_id = None
        for it in items:
            if not isinstance(it, dict): continue
            sd = str(it.get("side","")).lower()
            pid = it.get("positionId") or it.get("id")
            if not pid: continue
            if sd == "long" and not l_id: l_id = str(pid)
            if sd == "short" and not s_id: s_id = str(pid)
        if l_id or s_id:
            return l_id, s_id
    return None, None

def _needs_position_id(pos_json: Dict[str, Any]) -> bool:
    long_keys = ("longQty", "longSize", "longOpen", "longAvailable")
    short_keys = ("shortQty", "shortSize", "shortOpen", "shortAvailable")
    if any(k in pos_json for k in long_keys) and any(k in pos_json for k in short_keys):
        return True
    l_id, s_id = _extract_position_ids(pos_json)
    return bool(l_id or s_id)

# --------- Lookup by clientOid ----------
def get_order_by_client_oid(client_oid: str) -> Optional[Dict[str, Any]]:
    ok, js = _get(GET_BY_COID.format(clientOid=client_oid))
    if not ok: return None
    return js.get("data") or None

# --------- Post-only clamp ----------
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

# --------- Parse KuCoin band error (300012) ----------
_band_hi = re.compile(r"cannot be higher than\s*([0-9]*\.?[0-9]+)", re.I)
_band_lo = re.compile(r"cannot be lower than\s*([0-9]*\.?[0-9]+)", re.I)

def _parse_band(msg: str) -> Tuple[Optional[str], Optional[float]]:
    if not msg: return None, None
    m = _band_hi.search(msg)
    if m:
        try: return "max", float(m.group(1))
        except Exception: pass
    m = _band_lo.search(msg)
    if m:
        try: return "min", float(m.group(1))
        except Exception: pass
    return None, None

# ---------------- MARKET helper ----------------
def place_market_by_value(symbol: str, side: str, value_usdt: float, leverage: Optional[int] = None,
                          client_order_id: Optional[str] = None) -> Dict[str, Any]:
    _sync_server_time()
    lev = int(leverage or DEFAULT_LEVERAGE)
    value_qty = float(value_usdt) * float(lev)
    coid = client_order_id or str(_ts_ms())
    body = {
        "clientOid": coid,
        "symbol": symbol,
        "side": str(side).lower(),       # buy|sell
        "type": "market",
        "valueQty": f"{value_qty:.4f}",
        "leverage": str(lev),
    }
    log.info("[place_market] %s %s valueQty=%.2f", symbol, body["side"], value_qty)
    ok_http, js = _post(ORDERS_PATH, body)
    data = js.get("data") if isinstance(js, dict) else None
    order_id = data.get("orderId") if isinstance(data, dict) else None
    code = (js.get("code") or ""); msg = js.get("msg") or ""
    api_ok = bool(ok_http and code == "200000" and order_id)
    res = {"ok": api_ok, "code": code, "msg": msg, "orderId": order_id, "clientOid": coid, "data": (data or {})}
    return res

# ---------------- CANCEL / STATUS ----------------
def cancel_order(order_id: str) -> Dict[str, Any]:
    ok, js = _delete(f"{ORDERS_PATH}/{order_id}")
    return {"ok": ok and (js.get("code") == "200000"), "data": js.get("data")}

def get_order_status(order_id: str) -> Dict[str, Any]:
    ok, js = _get(f"{ORDERS_PATH}/{order_id}")
    data = js.get("data") if ok else None
    return {"ok": ok and (js.get("code") == "200000"), "data": data}

# ---------------- Place LIMIT (robuste) ----------------
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

    lev = int(leverage or DEFAULT_LEVERAGE)
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
    log.info("[positionMode] %s -> oneway/hedge autodetect (no positionSide sent)", symbol)

    coid = client_order_id or str(_ts_ms())
    s_low = str(side).lower()

    def _make_body(lev_force: Optional[int] = None, price_override: Optional[float] = None,
                   position_id: Optional[str] = None, force_post_only: Optional[bool] = None) -> Dict[str, Any]:
        use_px = float(price_override) if (price_override is not None) else float(px)
        body = {
            "clientOid": coid,
            "symbol": symbol,
            "side": s_low,
            "type": "limit",
            "price": f"{use_px:.12f}",
            "valueQty": f"{value_qty:.4f}",
            "timeInForce": "GTC",
            "postOnly": bool(post_only if force_post_only is None else force_post_only),
            "leverage": str(lev_force if lev_force is not None else lev),
        }
        if position_id:
            body["positionId"] = str(position_id)
        return body

    def _send(body: Dict[str, Any]) -> Dict[str, Any]:
        log.info("[place_limit] %s %s px=%s valueQty=%.2f postOnly=%s%s",
                 symbol, body.get("side"), body.get("price"), float(value_qty), body.get("postOnly"),
                 f" positionId={body.get('positionId')}" if "positionId" in body else "")
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

    # 1) tentative standard (NE JAMAIS envoyer positionSide)
    body = _make_body()
    resp = _send(body)

    # 2) leverage invalid (100001) → retry avec levier fallback
    if (not resp["ok"]) and ("Leverage parameter invalid" in str(resp.get("msg","")) or resp.get("code") == "100001"):
        lev_fb = int(DEFAULT_LEVERAGE or 5)
        if lev_fb == lev: lev_fb = 5 if lev != 5 else 3
        log.info("[leverage] retry %s with leverage=%s", symbol, lev_fb)
        resp_fb = _send(_make_body(lev_force=lev_fb))
        if resp_fb["ok"]: return resp_fb
        resp = resp_fb

    # 3) position mode mismatch (330011) → tenter avec positionId si dispo (hedge réel)
    if (not resp["ok"]) and (resp.get("code") == "330011"):
        if _needs_position_id(pos_raw):
            long_id, short_id = _extract_position_ids(pos_raw)
            want_id = long_id if s_low == "buy" else short_id
            log.info("[positionMode] retry %s with positionId=%s", symbol, want_id or "None")
            resp2 = _send(_make_body(position_id=want_id))
            if resp2["ok"]:
                return resp2
            resp = resp2
        else:
            log.info("[positionMode] no positionId available; keeping one-way semantics")

    # 4) prix hors bande (300012) → lire le seuil et reclamp; couper postOnly si autorisé
    if (not resp["ok"]) and (resp.get("code") == "300012"):
        kind, edge = _parse_band(resp.get("msg",""))
        px_retry = float(price)
        if kind == "max":  # buy trop haut (ou sell aussi haut)
            px_retry = edge - (tick * 0.5)
        elif kind == "min":  # sell trop bas
            px_retry = edge + (tick * 0.5)
        px_retry = _quantize_price(px_retry, tick, side)
        force_po = False if ALLOW_TAKER_ON_BAND_RETRY else None
        log.info("[price] retry %s band-kind=%s edge=%s px=%s (postOnly=%s)",
                 symbol, kind, edge, f"{px_retry:.12f}", False if force_po is False else post_only)
        resp3 = _send(_make_body(price_override=px_retry, force_post_only=force_po))
        return resp3

    return resp
