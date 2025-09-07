# kucoin_adapter.py — Futures REST adapter (auto marginMode) prêt à coller
import time
import hmac
import base64
import hashlib
from typing import Any, Dict, Optional

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

def _post(path: str, body: Optional[Dict[str, Any]]) -> Dict[str, Any]:
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
    return js

def _get(path: str) -> Dict[str, Any]:
    url = BASE + path
    with httpx.Client(timeout=10.0) as c:
        r = c.get(url, headers=_headers("GET", path, ""))
        try:
            js = r.json() if r.content else {}
        except Exception:
            js = {"code": str(r.status_code), "msg": r.text}
    if r.status_code >= 400:
        log.error("[GET %s] HTTP=%s %s", path, r.status_code, r.text[:200])
    return js

# ---------- metadata ----------
def get_symbol_meta(symbol: str) -> Dict[str, Any]:
    """
    Renvoie la fiche contrat KuCoin Futures: tick, lotSize, mark/last price...
    symbol doit être le contrat (ex: BTCUSDTM).
    """
    p = f"/api/v1/contracts/{symbol}"
    js = _get(p)
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
    # floor au tick inférieur (évite "Price parameter invalid")
    stepped = (int(float(price) / tick)) * tick
    prec = _price_precision(meta)
    return round(stepped, prec)

# ---------- margin mode detection ----------
def get_position(symbol: str) -> Dict[str, Any]:
    js = _get(f"/api/v1/position?symbol={symbol}")
    return js.get("data", {}) if isinstance(js, dict) else {}

def get_margin_mode(symbol: str) -> str:
    """
    Détermine le margin mode en interrogeant la position du contrat.
    Retourne "cross" si crossMode==True, sinon "isolated".
    """
    pos = get_position(symbol) or {}
    # KuCoin renvoie souvent 'crossMode': true/false
    cross = bool(pos.get("crossMode")) if isinstance(pos, dict) else False
    mm = "cross" if cross else "isolated"
    log.info("[marginMode] %s -> %s", symbol, mm)
    return mm

# ---------- orders ----------
def get_order_by_client_oid(client_oid: str) -> Optional[Dict[str, Any]]:
    js = _get(f"/api/v1/order/client-order/{client_oid}")
    # 404 => pas trouvé (ordre refusé), sinon code 200000 => OK
    if isinstance(js, dict) and str(js.get("code")) in ("200000", "200", "0", "None"):
        return js.get("data") or {}
    return None

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
    margin_mode: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Place un LIMIT avec notional (valueQty). Ajoute marginMode auto (cross/isolated).
    N'ENVOIE PAS 'leverage' pour éviter 100001.
    """
    meta = get_symbol_meta(symbol) or {}
    px = _round_price_to_tick(price, meta)
    side_api = side.lower().strip()
    if side_api == "long":  side_api = "buy"
    if side_api == "short": side_api = "sell"

    mm = (margin_mode or get_margin_mode(symbol)).lower()
    if mm not in ("cross", "isolated"):
        mm = "isolated"

    body = {
        "clientOid": str(_ts_ms()),
        "symbol": symbol,
        "type": "limit",
        "side": side_api,
        "price": f"{px:.12f}",
        "valueQty": f"{float(value_usdt):.2f}",
        "timeInForce": time_in_force.upper(),
        "reduceOnly": False,
        "postOnly": bool(post_only),
        "marginMode": mm,   # <<< clé pour éviter 330005
    }

    log.info("[place_limit] %s %s px=%s valueQty=%.2f postOnly=%s marginMode=%s",
             symbol, side_api, body["price"], float(value_usdt), body["postOnly"], mm)

    resp = _post("/api/v1/orders", body)
    data = resp.get("data") or {}
    ok = str(resp.get("code")) in ("200000", "200", "0", "None") and bool(data.get("orderId"))
    # journal utile en cas d’erreur
    if not ok:
        log.info("[kc.place_limit_order] ok=%s code=%s msg=%s clientOid=%s orderId=%s",
                 ok, resp.get("code"), resp.get("msg"), data.get("clientOid") or body["clientOid"], data.get("orderId"))
    return {
        "ok": ok,
        "code": resp.get("code"),
        "msg": resp.get("msg"),
        "orderId": data.get("orderId"),
        "clientOid": data.get("clientOid") or body["clientOid"],
        "data": data,
        "raw": resp,
    }

def place_market_order(
    symbol: str,
    side: str,
    value_usdt: float,
    reduce_only: bool = False,
    margin_mode: Optional[str] = None,
) -> Dict[str, Any]:
    """
    MARKET notional (valueQty). Ajoute marginMode auto. Pas de leverage.
    """
    side_api = side.lower().strip()
    if side_api == "long":  side_api = "buy"
    if side_api == "short": side_api = "sell"

    mm = (margin_mode or get_margin_mode(symbol)).lower()
    if mm not in ("cross", "isolated"):
        mm = "isolated"

    body = {
        "clientOid": str(_ts_ms()),
        "symbol": symbol,
        "type": "market",
        "side": side_api,
        "valueQty": f"{float(value_usdt):.2f}",
        "reduceOnly": bool(reduce_only),
        "marginMode": mm,
    }
    log.info("[place_market] %s %s valueQty=%.2f reduceOnly=%s marginMode=%s",
             symbol, side_api, float(value_usdt), reduce_only, mm)

    resp = _post("/api/v1/orders", body)
    data = resp.get("data") or {}
    ok = str(resp.get("code")) in ("200000", "200", "0", "None") and bool(data.get("orderId"))
    if not ok:
        log.info("[kc.place_market_order] ok=%s code=%s msg=%s clientOid=%s orderId=%s",
                 ok, resp.get("code"), resp.get("msg"), data.get("clientOid") or body["clientOid"], data.get("orderId"))
    return {
        "ok": ok,
        "code": resp.get("code"),
        "msg": resp.get("msg"),
        "orderId": data.get("orderId"),
        "clientOid": data.get("clientOid") or body["clientOid"],
        "data": data,
        "raw": resp,
    }

__all__ = [
    "get_symbol_meta",
    "get_position",
    "get_margin_mode",
    "get_order_by_client_oid",
    "place_limit_order",
    "place_market_order",
]
