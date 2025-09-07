# kucoin_adapter.py — version "ancien style robuste" prête à coller
# - Signature v2 (timestamp + method + path + body)
# - clientOid = timestamp
# - valueQty = ORDER_VALUE_USDT * leverage (comme avant)
# - Arrondi prix au tick (tickSize/priceIncrement/pricePrecision)
# - Quantification directionnelle (BUY=floor, SELL=ceil)
# - Détection/flip crossMode si mismatch
# - Retry si 100001 (leverage invalid)
# - Vérification par clientOid

import time
import hmac
import base64
import hashlib
import math
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

# --------- Horloge (offset serveur) ----------
_SERVER_OFFSET = 0.0

def _sync_server_time() -> None:
    global _SERVER_OFFSET
    try:
        with httpx.Client(timeout=5.0) as c:
            r = c.get(BASE + TIME_PATH)
            r.raise_for_status()
            server_ms = int(r.json().get("data", 0))
            _SERVER_OFFSET = (server_ms / 1000.0) - time.time()
            log.info(f"[time] offset={_SERVER_OFFSET:.3f}s")
    except Exception as e:
        log.warning(f"time sync failed: {e}")

def _ts_ms() -> int:
    return int((time.time() + _SERVER_OFFSET) * 1000)

# --------- Signature v2 ----------
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

# --------- HTTP helpers ----------
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

def _get(path: str) -> Tuple[bool, Dict[str, Any]]:
    url = BASE + path
    hdrs = _headers("GET", path, "")
    try:
        with httpx.Client(timeout=10.0) as c:
            r = c.get(url, headers=hdrs)
            ok = (r.status_code == 200)
            data = r.json() if r.content else {}
            if not ok:
                log.error(f"[GET {path}] HTTP={r.status_code} {r.text[:200]}")
            return ok, (data if isinstance(data, dict) else {})
    except Exception as e:
        log.error(f"[GET {path}] EXC={e}")
        return False, {"error": str(e)}

# --------- Métadonnées contrat / position ----------
def get_symbol_meta(symbol: str) -> Dict[str, Any]:
    """Retourne /contracts/{symbol} (KuCoin Futures)."""
    path = f"{CNTR_PATH}/{symbol}"
    ok, js = _get(path)
    if ok:
        return js.get("data", {}) or {}
    return {}

def _price_increment(symbol: str) -> float:
    """
    Tick robuste:
      1) contracts/{symbol}: tickSize -> priceIncrement
      2) contracts/active fallback
      3) pricePrecision -> 10^-pp
      4) ultime fallback non nul
    """
    def _to_f(x) -> float:
        try:
            return float(x)
        except Exception:
            return 0.0

    meta = get_symbol_meta(symbol) or {}
    tick = meta.get("tickSize", None)
    if not _to_f(tick):
        tick = meta.get("priceIncrement", None)

    t = _to_f(tick)
    if t > 0:
        return t

    # fallback active list
    ok, js = _get(f"{CNTR_PATH}/active")
    if ok:
        for it in js.get("data", []) or []:
            if str(it.get("symbol", "")).strip().upper() == symbol.upper():
                t = _to_f(it.get("tickSize") or it.get("priceIncrement"))
                if t > 0:
                    return t
                pp = it.get("pricePrecision")
                try:
                    pp = int(pp)
                    if pp is not None and pp >= 0:
                        return 10 ** (-pp)
                except Exception:
                    pass

    # derive from pricePrecision in meta
    pp = meta.get("pricePrecision", None)
    try:
        pp = int(pp)
        if pp is not None and pp >= 0:
            return 10 ** (-pp)
    except Exception:
        pass

    # ultimate non-zero (évite "multiple of 0")
    return 1e-8

def _quantize_price(price: float, tick: float, side: str) -> float:
    """
    Quantifie le prix au multiple exact de tick.
    BUY  -> floor (reste passif en postOnly)
    SELL -> ceil  (reste passif en postOnly)
    """
    price = float(price)
    tick  = float(tick)
    if tick <= 0:
        return price
    steps = price / tick
    if str(side).lower() == "buy":
        qsteps = math.floor(steps + 1e-12)
    else:
        qsteps = math.ceil(steps - 1e-12)
    return float(qsteps) * tick

def _position_mode(symbol: str) -> Optional[bool]:
    """
    Retourne crossMode (True/False) si dispo.
    GET /api/v1/position?symbol=...
    """
    path = f"{POS_PATH}?symbol={symbol}"
    ok, js = _get(path)
    if not ok:
        return None
    d = js.get("data") or {}
    try:
        cm = d.get("crossMode")
        if cm is None:
            return None
        return bool(cm)
    except Exception:
        return None

# --------- Vérification ordre par clientOid ----------
def get_order_by_client_oid(client_oid: str) -> Optional[Dict[str, Any]]:
    path = GET_BY_COID.format(clientOid=client_oid)
    ok, js = _get(path)
    if not ok:
        return None
    return js.get("data") or None

# --------- Place LIMIT (style ancien + robustesse) ----------
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
    """
    - LIMIT avec valueQty = value_usdt * leverage (ancien comportement)
    - Envoie toujours 'leverage' (string)
    - Prix quantifié au tick (directionnel)
    - Détection du crossMode courant; si mismatch → flip et retry
    - Retry si 100001 (Leverage invalid) avec levier fallback
    - Renvoie {"ok":bool, "code":..., "msg":..., "orderId":..., "clientOid":..., "data": {...}}
    """
    _sync_server_time()

    lev = int(leverage or getattr(SETTINGS, "default_leverage", 5))
    value_qty = float(value_usdt) * float(lev)  # notionnel = marge * levier

    tick = _price_increment(symbol)
    px   = _quantize_price(float(price), tick, side)

    # crossMode: si non fourni → lire /position
    if cross_mode is None:
        cm = _position_mode(symbol)
        cross_mode = cm if cm is not None else False  # défaut: isolated

    coid = client_order_id or str(_ts_ms())

    def _make_body(cross_flag: bool, lev_force: Optional[int] = None) -> Dict[str, Any]:
        body = {
            "clientOid": coid,
            "symbol": symbol,
            "side": side.lower(),
            "type": "limit",
            "price": f"{px:.12f}",
            "valueQty": f"{value_qty:.4f}",
            "timeInForce": "GTC",
            "postOnly": bool(post_only),
            "leverage": str(lev_force if lev_force is not None else lev),
            "crossMode": bool(cross_flag),
        }
        return body

    def _send(body: Dict[str, Any]) -> Dict[str, Any]:
        log.info(
            "[place_limit] %s %s px=%s valueQty=%.2f postOnly=%s crossMode=%s",
            symbol, body.get("side"), body.get("price"),
            float(value_qty), body.get("postOnly"), body.get("crossMode"),
        )
        ok, js = _post(ORDERS_PATH, body)
        data = js.get("data") if isinstance(js, dict) else None
        order_id = None
        if isinstance(data, dict):
            order_id = data.get("orderId")
        code = (js.get("code") or "")
        msg  = js.get("msg") or ""
        res = {
            "ok": ok and (code == "200000"),
            "code": code,
            "msg": msg,
            "orderId": order_id,
            "clientOid": body.get("clientOid"),
            "data": (data or {}),
        }
        if not res["ok"]:
            log.info("[kc.place_limit_order] ok=%s code=%s msg=%s clientOid=%s orderId=%s",
                     res["ok"], res["code"], res["msg"], res["clientOid"], res["orderId"])
        return res

    # 1) tentative avec cross_mode déterminé
    body = _make_body(cross_mode)
    resp = _send(body)

    # 2) mismatch margin mode → flip et retenter
    if (not resp["ok"]) and any(x in str(resp["msg"]) for x in [
        "margin mode does not match", "margin mode", "330005", "400100"
    ]):
        flipped = not bool(cross_mode)
        log.info("[marginMode] %s -> %s", symbol, ("cross" if flipped else "isolated"))
        body = _make_body(flipped)
        resp = _send(body)

    # 3) leverage invalid → retenter avec levier fallback
    if (not resp["ok"]) and ("Leverage parameter invalid" in str(resp["msg"]) or resp["code"] == "100001"):
        lev_fb = int(getattr(SETTINGS, "default_leverage", 5) or 5)
        if lev_fb == lev:
            lev_fb = 5 if lev != 5 else 3
        log.info("[leverage] retry %s with leverage=%s", symbol, lev_fb)
        body = _make_body(body.get("crossMode", False), lev_force=lev_fb)
        resp = _send(body)

    return resp
