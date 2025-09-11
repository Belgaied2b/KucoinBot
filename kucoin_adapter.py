# kucoin_adapter.py — module "env-based" prêt à coller (Railway)
# - Lit les clés via env: KUCOIN_API_KEY / KUCOIN_API_SECRET / KUCOIN_API_PASSPHRASE
# - Signature V2 (TIMESTAMP + METHOD + PATH + BODY)
# - 1er essai: jamais positionSide, jamais crossMode
# - Retries: 100001 (leverage fallback), 300012 (clamp prix), 330011 (si hedge → positionSide/positionId)
# - Ticker pour top of book (depth1 peut 404)
# - Logs détaillés quand KC_VERBOSE=1 (payload sortant + réponses API tronquées)
# - Fail-fast si variables d’env manquantes (avec logs masqués)

import os, time, hmac, base64, hashlib, math, logging
from typing import Any, Dict, Optional, Tuple, List

import httpx
import ujson as json

# ====== LOGGING ======
log = logging.getLogger("kucoin.adapter")
if not log.handlers:
    _h = logging.StreamHandler()
    _fmt = logging.Formatter("%(asctime)s | %(levelname)5s | %(name)s | [-] %(message)s")
    _h.setFormatter(_fmt)
    log.addHandler(_h)

KC_VERBOSE = os.getenv("KC_VERBOSE", "0") == "1"
log.setLevel(logging.DEBUG if KC_VERBOSE else logging.INFO)

# ====== CONFIG (env) ======
def _mask(s: Optional[str]) -> str:
    if not s:
        return "MISSING"
    s = str(s)
    if len(s) <= 8:
        return s[0] + "***" + s[-1]
    return s[:4] + "..." + s[-4:]

def _require_env(name: str) -> str:
    v = os.getenv(name, "").strip()
    if not v:
        log.error("ENV %s manquante. Vérifie Railway → Variables.", name)
        raise RuntimeError(f"Missing required env var: {name}")
    return v

KUCOIN_API_KEY        = _require_env("KUCOIN_API_KEY")
KUCOIN_API_SECRET     = _require_env("KUCOIN_API_SECRET")
KUCOIN_API_PASSPHRASE = _require_env("KUCOIN_API_PASSPHRASE")

# Sanity log (masqué)
log.info(
    "KUCOIN_API_KEY=%s KUCOIN_API_SECRET=%s KUCOIN_API_PASSPHRASE=%s",
    _mask(KUCOIN_API_KEY), _mask(KUCOIN_API_SECRET), _mask(KUCOIN_API_PASSPHRASE)
)

BASE = os.getenv("KUCOIN_BASE_URL", "https://api-futures.kucoin.com").rstrip("/")
ORDERS_PATH = "/api/v1/orders"
POS_PATH    = "/api/v1/position"
CNTR_PATH   = "/api/v1/contracts"
TIME_PATH   = "/api/v1/timestamp"
GET_BY_COID = "/api/v1/order/client-order/{clientOid}"
TICKER_PATH = "/api/v1/ticker"

DEFAULT_LEVERAGE = int(os.getenv("DEFAULT_LEVERAGE", "10"))
DEFAULT_TICK_FALLBACK = float(os.getenv("DEFAULT_TICK_SIZE", "0.00000001"))
HTTP_TIMEOUT = float(os.getenv("HTTP_TIMEOUT_SEC", "10.0"))

# ====== TIME SYNC ======
_SERVER_OFFSET = 0.0

def _sync_server_time() -> None:
    global _SERVER_OFFSET
    try:
        with httpx.Client(timeout=5.0) as c:
            r = c.get(BASE + TIME_PATH)
            raw = r.text[:400]
            try:
                js = r.json()
            except Exception:
                js = {}
            if r.status_code == 200:
                server_ms = int((js or {}).get("data", 0))
                _SERVER_OFFSET = (server_ms / 1000.0) - time.time()
                log.info("[time] offset=%.3fs http=200 data=%s", _SERVER_OFFSET, (js or {}))
            else:
                log.error("[time] HTTP=%s body=%s", r.status_code, raw)
    except Exception as e:
        log.warning("time sync failed: %s", e)

def _ts_ms() -> int:
    return int((time.time() + _SERVER_OFFSET) * 1000)

# ====== SIGNATURE V2 ======
def _b64_hmac_sha256(secret: str, payload: str) -> str:
    return base64.b64encode(
        hmac.new(secret.encode("utf-8"), payload.encode("utf-8"), hashlib.sha256).digest()
    ).decode("utf-8")

def _headers(method: str, path: str, body_str: str = "") -> Dict[str, str]:
    ts = str(_ts_ms())
    sig = _b64_hmac_sha256(KUCOIN_API_SECRET, ts + method.upper() + path + (body_str or ""))
    psp = _b64_hmac_sha256(KUCOIN_API_SECRET, KUCOIN_API_PASSPHRASE)
    return {
        "KC-API-KEY": KUCOIN_API_KEY,
        "KC-API-SIGN": sig,
        "KC-API-TIMESTAMP": ts,
        "KC-API-PASSPHRASE": psp,
        "KC-API-KEY-VERSION": "2",
        "Accept": "application/json",
        "Content-Type": "application/json",
        "User-Agent": "bot/kucoin-adapter-env",
    }

# ====== HTTP HELPERS ======
def _log_http_outcome(verb: str, path: str, r: httpx.Response):
    raw = r.text[:800] if r.text else ""
    try:
        js = r.json() if r.content else {}
    except Exception:
        js = {}
    code = (js or {}).get("code")
    msg  = (js or {}).get("msg")
    if r.status_code == 200:
        log.debug("[%s %s] HTTP=200 code=%s msg=%s", verb, path, code, msg)
        if KC_VERBOSE:
            log.debug("[%s %s] body=%s", verb, path, (js or {}))
    else:
        log.error("[%s %s] HTTP=%s body=%s", verb, path, r.status_code, raw[:800])
    return js

def _post(path: str, body: Optional[Dict[str, Any]]) -> Tuple[bool, Dict[str, Any]]:
    url = BASE + path
    body_str = "" if body is None else json.dumps(body, separators=(",", ":"), ensure_ascii=False)
    hdrs = _headers("POST", path, body_str)
    if KC_VERBOSE:
        log.debug("[POST %s] payload=%s", path, body)
    try:
        with httpx.Client(timeout=HTTP_TIMEOUT) as c:
            r = c.post(url, headers=hdrs, content=(body_str.encode("utf-8") if body_str else None))
            js = _log_http_outcome("POST", path, r)
            return (r.status_code == 200), (js if isinstance(js, dict) else {})
    except Exception as e:
        log.error("[POST %s] EXC=%s", path, e)
        return False, {"error": str(e)}

def _get(path: str, params: Optional[Dict[str, Any]] = None) -> Tuple[bool, Dict[str, Any]]:
    url = BASE + path
    hdrs = _headers("GET", path, "")
    try:
        with httpx.Client(timeout=HTTP_TIMEOUT) as c:
            r = c.get(url, headers=hdrs, params=params)
            js = _log_http_outcome("GET", path, r)
            return (r.status_code == 200), (js if isinstance(js, dict) else {})
    except Exception as e:
        log.error("[GET %s] EXC=%s", path, e)
        return False, {"error": str(e)}

# ====== MARKET DATA ======
def get_orderbook_top(symbol: str) -> Dict[str, Optional[float]]:
    ok, js = _get(TICKER_PATH, params={"symbol": symbol})
    if ok and isinstance(js, dict) and isinstance(js.get("data"), dict):
        d = js["data"]
        def _f(x):
            try: return float(x)
            except Exception: return None
        out = {
            "bestBid": _f(d.get("bestBidPrice") or d.get("buy") or d.get("bestBid")),
            "bestAsk": _f(d.get("bestAskPrice") or d.get("sell") or d.get("bestAsk")),
            "bidSize": _f(d.get("bestBidSize")),
            "askSize": _f(d.get("bestAskSize")),
        }
        if KC_VERBOSE:
            log.debug("[ticker] %s %s", symbol, out)
        return out
    return {"bestBid": None, "bestAsk": None, "bidSize": None, "askSize": None}

def get_orderbook_levels(symbol: str, depth: int = 5) -> List[Dict[str, Any]]:
    # endpoints depth/level2 des futures sont instables — on évite (compat SFI)
    return []

# ====== META / TICK ======
def get_symbol_meta(symbol: str) -> Dict[str, Any]:
    ok, js = _get(f"{CNTR_PATH}/{symbol}")
    if ok:
        data = js.get("data", {}) or {}
        if KC_VERBOSE:
            log.debug("[contracts/%s] %s", symbol, data)
        return data
    return {}

def _safe_tick_from_meta(d: Dict[str, Any]) -> float:
    def _to_f(x) -> float:
        try: return float(x)
        except Exception: return 0.0
    tick = _to_f(d.get("tickSize")) or _to_f(d.get("priceIncrement"))
    if tick and tick > 0: return tick
    pp = d.get("pricePrecision")
    try:
        pp = int(pp)
        if pp is not None and pp >= 0: return 10 ** (-pp)
    except Exception:
        pass
    return DEFAULT_TICK_FALLBACK

def _price_increment(symbol: str) -> float:
    meta = get_symbol_meta(symbol) or {}
    t = _safe_tick_from_meta(meta)
    if t > 0: return t
    ok, js = _get(f"{CNTR_PATH}/active")
    if ok:
        for it in js.get("data", []) or []:
            if str(it.get("symbol", "")).strip().upper() == symbol.upper():
                return _safe_tick_from_meta(it)
    return DEFAULT_TICK_FALLBACK

def _quantize_price(price: float, tick: float, side: str) -> float:
    price = float(price); tick = float(tick)
    if tick <= 0: return price
    steps = price / tick
    return (math.floor(steps + 1e-12) if str(side).lower() == "buy"
            else math.ceil(steps - 1e-12)) * tick

def _clamp_postonly_price(symbol: str, side: str, raw_px: float, tick: float) -> float:
    quotes = get_orderbook_top(symbol)
    bb, ba = quotes.get("bestBid"), quotes.get("bestAsk")
    px = float(raw_px)
    s = str(side).lower()
    if bb is None and ba is None:
        return _quantize_price(px, tick, side)
    if s == "buy":
        anchor = bb if bb is not None else ba
        if anchor is not None:
            px = min(px, anchor - tick)
    else:
        anchor = ba if ba is not None else bb
        if anchor is not None:
            px = max(px, anchor + tick)
    qpx = _quantize_price(px, tick, side)
    if KC_VERBOSE:
        log.debug("[clamp] %s %s raw=%.12f → clamped=%.12f (bb=%s ba=%s, tick=%s)",
                  symbol, side, raw_px, qpx, bb, ba, tick)
    return qpx

# ====== POSITION / MODE ======
def _get_position_raw(symbol: str) -> Dict[str, Any]:
    ok, js = _get(f"{POS_PATH}?symbol={symbol}")
    if not ok or not isinstance(js, dict): return {}
    data = js.get("data")
    if isinstance(data, dict): return data
    if isinstance(data, list) and data: return data[0]
    return {}

def _detect_hedge(pos_json: Dict[str, Any]) -> Tuple[bool, str]:
    if not pos_json:
        return False, "empty-payload"
    for k in ("positionMode", "posMode", "mode"):
        v = pos_json.get(k)
        if isinstance(v, str):
            v_low = v.lower()
            if "hedge" in v_low: return True, f"{k}=hedge"
            if "one" in v_low or "single" in v_low: return False, f"{k}=oneway"
    if isinstance(pos_json.get("isHedgeMode"), bool):
        return (pos_json["isHedgeMode"], "isHedgeMode")
    long_keys = ("longQty", "longSize", "longOpen", "longAvailable")
    short_keys = ("shortQty", "shortSize", "shortOpen", "shortAvailable")
    if any(k in pos_json for k in long_keys) and any(k in pos_json for k in short_keys):
        return True, "split-long-short-metrics"
    items = pos_json.get("positions") or pos_json.get("items") or pos_json.get("data")
    if isinstance(items, list) and len(items) >= 2:
        sides = {str(x.get("side","")).lower() for x in items if isinstance(x, dict)}
        if "long" in sides and "short" in sides:
            return True, "positions-array-long+short"
    return False, "no-hedge-indicator"

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
        if l_id or s_id: return l_id, s_id
    return None, None

def get_order_by_client_oid(client_oid: str) -> Optional[Dict[str, Any]]:
    ok, js = _get(GET_BY_COID.format(clientOid=client_oid))
    if not ok: return None
    return js.get("data") or None

# ====== PLACE LIMIT (API PRINCIPALE) ======
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
    cross_mode: Optional[bool] = None,  # ignoré à l’envoi
) -> Dict[str, Any]:
    _sync_server_time()

    lev = int(leverage or DEFAULT_LEVERAGE)
    value_qty = float(value_usdt) * float(lev)

    tick = _price_increment(symbol)
    px_q = _quantize_price(float(price), tick, side)
    px   = _clamp_postonly_price(symbol, side, px_q, tick) if post_only else px_q

    if KC_VERBOSE:
        log.debug("[px] %s %s input=%.12f tick=%.12f → quant=%.12f → final=%.12f postOnly=%s",
                  symbol, side, float(price), tick, px_q, px, post_only)

    # position snapshot
    pos_raw = _get_position_raw(symbol)
    is_hedge, hedge_reason = _detect_hedge(pos_raw)
    log.info("[position] %s -> hedge=%s reason=%s (no positionSide on first try)", symbol, is_hedge, hedge_reason)

    coid = client_order_id or str(_ts_ms())
    s_low = str(side).lower()

    def _make_body(lev_force: Optional[int] = None,
                   price_override: Optional[float] = None,
                   position_side: Optional[str] = None,
                   position_id: Optional[str] = None) -> Dict[str, Any]:
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
        # 1er essai: pas de positionSide / crossMode
        if position_side:
            body["positionSide"] = position_side
        if position_id:
            body["positionId"] = str(position_id)
        return body

    def _send(body: Dict[str, Any], tag: str) -> Dict[str, Any]:
        log.info(
            "[place_limit%s] %s %s px=%s valueQty=%.4f postOnly=%s%s%s",
            tag, symbol, body.get("side"), body.get("price"),
            float(value_qty), body.get("postOnly"),
            f" positionSide={body.get('positionSide')}" if "positionSide" in body else "",
            f" positionId={body.get('positionId')}" if "positionId" in body else ""
        )
        ok_http, js = _post(ORDERS_PATH, body)
        data = js.get("data") if isinstance(js, dict) else None
        order_id = data.get("orderId") if isinstance(data, dict) else None
        code = (js.get("code") or ""); msg = js.get("msg") or ""
        api_ok = bool(ok_http and code == "200000" and order_id)
        res = {"ok": api_ok, "code": code, "msg": msg, "orderId": order_id, "clientOid": body.get("clientOid"), "data": (data or {})}
        if not res["ok"] and KC_VERBOSE:
            log.debug("[kc.place_limit_order%s] resp=%s", tag, js)
        if not res["ok"]:
            log.info("[kc.place_limit_order%s] ok=%s code=%s msg=%s clientOid=%s orderId=%s",
                     tag, res["ok"], res["code"], res["msg"], res["clientOid"], res["orderId"])
        return res

    # 1) Premier essai (legacy sans positionSide)
    resp = _send(_make_body(), tag="")

    # 2) Leverage invalid (100001) → fallback
    if (not resp["ok"]) and ("Leverage parameter invalid" in str(resp.get("msg","")) or resp.get("code") == "100001"):
        lev_fb = DEFAULT_LEVERAGE if lev != DEFAULT_LEVERAGE else 5
        log.info("[leverage] retry %s with leverage=%s", symbol, lev_fb)
        resp2 = _send(_make_body(lev_force=lev_fb), tag=":lev")
        if resp2["ok"]:
            return resp2
        resp = resp2

    # 3) Prix hors bande (300012) → reclamp & retry
    if (not resp["ok"]) and (resp.get("code") == "300012"):
        px_retry = _clamp_postonly_price(symbol, side, float(price), tick)
        log.info("[price] retry %s with passive px=%s (clamped)", symbol, f"{px_retry:.12f}")
        resp3 = _send(_make_body(price_override=px_retry), tag=":px")
        if resp3["ok"]:
            return resp3
        resp = resp3

    # 4) Mode mismatch (330011) → si *hedge détecté*, ajouter positionSide (+ positionId si dispo)
    if (not resp["ok"]) and (resp.get("code") == "330011"):
        if is_hedge:
            pos_long_id, pos_short_id = _extract_position_ids(pos_raw)
            ps = "long" if s_low == "buy" else "short"
            use_pid = pos_long_id if ps == "long" else pos_short_id
            log.info("[mode] 330011 → retry with positionSide=%s positionId=%s (hedge_detected=%s reason=%s)",
                     ps, use_pid or "None", is_hedge, hedge_reason)
            resp4 = _send(_make_body(position_side=ps, position_id=use_pid), tag=":ps")
            return resp4
        else:
            log.info("[mode] 330011 but hedge not detected → keep one-way semantics and return error.")
            return resp

    return resp

# ====== STUBS (compat SFI) ======
def cancel_order(order_id: str) -> Dict[str, Any]:
    try:
        path = f"/api/v1/orders/{order_id}"
        url = BASE + path
        hdrs = _headers("DELETE", path, "")
        with httpx.Client(timeout=6.0) as c:
            r = c.delete(url, headers=hdrs)
            js = _log_http_outcome("DELETE", path, r)
            return {"ok": r.status_code == 200, "data": js}
    except Exception as e:
        log.error("[cancel] EXC=%s", e)
        return {"ok": False, "error": str(e)}

def replace_order(order_id: str, new_price: float) -> Dict[str, Any]:
    c = cancel_order(order_id)
    return {"replaced": False, "cancelled": bool(c.get("ok"))}

def get_order_status(order_id: str) -> Dict[str, Any]:
    return {"status": "unknown"}
