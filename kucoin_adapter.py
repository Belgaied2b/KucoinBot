# kucoin_adapter.py — Futures REST adapter (crossMode auto + retry) prêt à coller
import time
import hmac
import base64
import hashlib
from typing import Any, Dict, Optional, Tuple

import httpx
import ujson as json

from config import SETTINGS
from logger_utils import get_logger

log = get_logger("kucoin.adapter")

BASE = SETTINGS.kucoin_base_url.rstrip("/")

# ---------- low-level signing ----------
def _ts_ms() -> int:
    return int(time.time() * 1000)

def _b64_hmac_sha256(secret: str, payload: str) -> str:
    return base64.b64encode(
        hmac.new(secret.encode("utf-8"), payload.encode("utf-8"), hashlib.sha256).digest()
    ).decode("utf-8")

def _headers(method: str, path: str, body: str = "") -> Dict[str, str]:
    ts = str(_ts_ms())
    sig = _b64_hmac_sha256(SETTINGS.kucoin_secret, ts + method.upper() + path + body)
    psp = _b64_hmac_sha256(SETTINGS.kucoin_secret, SETTINGS.kucoin_passphrase)
    return {
        "KC-API-KEY": SETTINGS.kucoin_key,
        "KC-API-SIGN": sig,
        "KC-API-TIMESTAMP": ts,
        "KC-API-PASSPHRASE": psp,
        "KC-API-KEY-VERSION": "2",
        "Accept": "application/json",
        "Content-Type": "application/json",
        "User-Agent": "runner/kucoin-adapter",
    }

def _post(path: str, body: Optional[Dict[str, Any]]) -> Tuple[int, Dict[str, Any]]:
    url = BASE + path
    body_str = "" if body is None else json.dumps(body, separators=(",", ":"), ensure_ascii=False)
    with httpx.Client(timeout=10.0) as c:
        r = c.post(url, headers=_headers("POST", path, body_str), content=(body_str.encode("utf-8") if body_str else None))
        try:
            js = r.json() if r.content else {}
        except Exception:
            js = {"code": str(r.status_code), "msg": r.text}
    if r.status_code >= 400:
        log.error("[POST %s] HTTP=%s %s", path, r.status_code, r.text[:200])
    return r.status_code, js

def _get(path: str) -> Tuple[int, Dict[str, Any]]:
    url = BASE + path
    with httpx.Client(timeout=10.0) as c:
        r = c.get(url, headers=_headers("GET", path, ""))
        try:
            js = r.json() if r.content else {}
        except Exception:
            js = {"code": str(r.status_code), "msg": r.text}
    if r.status_code >= 400:
        log.error("[GET %s] HTTP=%s %s", path, r.status_code, r.text[:200])
    return r.status_code, js

# ---------- metadata ----------
def get_symbol_meta(symbol: str) -> Dict[str, Any]:
    """
    Renvoie la fiche contrat KuCoin Futures: tick, lotSize...
    symbol doit être le contrat (ex: BTCUSDTM).
    """
    _, js = _get(f"/api/v1/contracts/{symbol}")
    return js.get("data", {}) if isinstance(js, dict) else {}

def _price_precision(meta: Dict[str, Any]) -> int:
    inc = float(meta.get("priceIncrement", meta.get("tickSize", 0.0)) or 0.0)
    if inc <= 0:
        return 4
    s = f"{inc:.12f}".rstrip("0").rstrip(".")
    return len(s.split(".")[1]) if "." in s else 0

def _round_price_to_tick(price: float, meta: Dict[str, Any]) -> float:
    tick = float(meta.get("priceIncrement", meta.get("tickSize", 0.0)) or 0.0)
    if tick <= 0:
        return float(price)
    # floor au tick inférieur
    stepped = (int(float(price) / tick)) * tick
    prec = _price_precision(meta)
    return round(stepped, prec)

# ---------- position / margin mode ----------
def get_position(symbol: str) -> Dict[str, Any]:
    _, js = _get(f"/api/v1/position?symbol={symbol}")
    return js.get("data", {}) if isinstance(js, dict) else {}

def detect_cross_mode(symbol: str) -> Optional[bool]:
    """
    Retourne True si cross, False si isolated, None si inconnu (pas de position).
    """
    pos = get_position(symbol) or {}
    if not isinstance(pos, dict) or pos == {}:
        log.info("[marginMode] %s -> unknown (no position)", symbol)
        return None
    cross = bool(pos.get("crossMode"))
    log.info("[marginMode] %s -> %s", symbol, "cross" if cross else "isolated")
    return cross

# ---------- helpers ----------
def _side_to_api(side: str) -> str:
    s = side.lower().strip()
    if s == "long":  return "buy"
    if s == "short": return "sell"
    return s

def _ok_from_resp(resp: Dict[str, Any]) -> bool:
    code = str(resp.get("code"))
    data = resp.get("data") or {}
    return (code in ("200000", "200", "0", "None")) and bool(data.get("orderId"))

def _pack_result(resp: Dict[str, Any], fallback_client_oid: Optional[str] = None) -> Dict[str, Any]:
    data = resp.get("data") or {}
    return {
        "ok": _ok_from_resp(resp),
        "code": resp.get("code"),
        "msg": resp.get("msg"),
        "orderId": data.get("orderId"),
        "clientOid": data.get("clientOid") or fallback_client_oid,
        "data": data,
        "raw": resp,
    }

def _should_flip_mode(code: Any, msg: Any) -> bool:
    code_s = str(code)
    m = str(msg or "").lower()
    # codes/msgs observés quand le mode ne correspond pas
    return (code_s in ("330005", "400100")) or ("margin mode" in m and "match" in m)

# ---------- orders ----------
def get_order_by_client_oid(client_oid: str) -> Optional[Dict[str, Any]]:
    status, js = _get(f"/api/v1/order/client-order/{client_oid}")
    if status == 404:
        log.error("[GET /api/v1/order/client-order/%s] HTTP=404 %s", client_oid, json.dumps(js)[:200])
        return None
    if isinstance(js, dict) and str(js.get("code")) in ("200000", "200", "0", "None"):
        return js.get("data") or {}
    return None

def _place_limit_once(
    symbol: str,
    side_api: str,
    price: float,
    value_usdt: float,
    post_only: bool,
    tif: str,
    cross_mode: Optional[bool],
) -> Dict[str, Any]:
    meta = get_symbol_meta(symbol) or {}
    px = _round_price_to_tick(price, meta)
    body = {
        "clientOid": str(_ts_ms()),
        "symbol": symbol,
        "type": "limit",
        "side": side_api,
        "price": f"{px:.12f}",
        "valueQty": f"{float(value_usdt):.2f}",
        "timeInForce": tif,
        "reduceOnly": False,
        "postOnly": bool(post_only),
    }
    if cross_mode is not None:
        body["crossMode"] = bool(cross_mode)

    log.info("[place_limit] %s %s px=%s valueQty=%.2f postOnly=%s%s",
             symbol, side_api, body["price"], float(value_usdt), body["postOnly"],
             f" crossMode={body.get('crossMode')}" if "crossMode" in body else "")

    _, resp = _post("/api/v1/orders", body)
    if not _ok_from_resp(resp):
        data = resp.get("data") or {}
        log.info("[kc.place_limit_order] ok=%s code=%s msg=%s clientOid=%s orderId=%s",
                 False, resp.get("code"), resp.get("msg"), data.get("clientOid") or body["clientOid"], data.get("orderId"))
    return resp

def place_limit_order(
    symbol: str,
    side: str,
    price: float,
    value_usdt: float,
    sl: Optional[float] = None,
    tp1: Optional[float] = None,
    tp2: Optional[float] = None,
    post_only: bool = True,
    time_in_force: str = "GTC",
) -> Dict[str, Any]:
    """
    LIMIT notional (valueQty). Utilise crossMode (bool) et RETENTE en inversant si mismatch.
    N'envoie PAS 'leverage'.
    """
    side_api = _side_to_api(side)
    tif = time_in_force.upper()

    cross_guess = detect_cross_mode(symbol)  # None si inconnu
    # 1st try
    resp1 = _place_limit_once(symbol, side_api, price, value_usdt, post_only, tif, cross_guess)
    if _ok_from_resp(resp1):
        return _pack_result(resp1)

    # mismatch ? flip et retente 1 fois
    if _should_flip_mode(resp1.get("code"), resp1.get("msg")):
        flipped = (not cross_guess) if cross_guess is not None else True  # si inconnu, tente cross=True
        log.info("[place_limit] retry with crossMode=%s", flipped)
        resp2 = _place_limit_once(symbol, side_api, price, value_usdt, post_only, tif, flipped)
        return _pack_result(resp2, fallback_client_oid=(resp2.get("data") or {}).get("clientOid"))

    return _pack_result(resp1)

def place_market_order(
    symbol: str,
    side: str,
    value_usdt: float,
    reduce_only: bool = False,
) -> Dict[str, Any]:
    """
    MARKET notional (valueQty). crossMode auto + retry flip si mismatch.
    """
    side_api = _side_to_api(side)
    cross_guess = detect_cross_mode(symbol)

    def _once(cm: Optional[bool]) -> Dict[str, Any]:
        body = {
            "clientOid": str(_ts_ms()),
            "symbol": symbol,
            "type": "market",
            "side": side_api,
            "valueQty": f"{float(value_usdt):.2f}",
            "reduceOnly": bool(reduce_only),
        }
        if cm is not None:
            body["crossMode"] = bool(cm)
        log.info("[place_market] %s %s valueQty=%.2f reduceOnly=%s%s",
                 symbol, side_api, float(value_usdt), reduce_only,
                 f" crossMode={body.get('crossMode')}" if "crossMode" in body else "")
        _, resp = _post("/api/v1/orders", body)
        return resp

    resp1 = _once(cross_guess)
    if _ok_from_resp(resp1):
        return _pack_result(resp1)
    if _should_flip_mode(resp1.get("code"), resp1.get("msg")):
        flipped = (not cross_guess) if cross_guess is not None else True
        log.info("[place_market] retry with crossMode=%s", flipped)
        resp2 = _once(flipped)
        return _pack_result(resp2, fallback_client_oid=(resp2.get("data") or {}).get("clientOid"))
    return _pack_result(resp1)

__all__ = [
    "get_symbol_meta",
    "get_position",
    "detect_cross_mode",
    "get_order_by_client_oid",
    "place_limit_order",
    "place_market_order",
]
