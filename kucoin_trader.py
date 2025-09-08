# -*- coding: utf-8 -*-
"""
kucoin_trader.py — simple & robuste (comme avant + fixes 330005/330011)
- Signature V2
- valueQty = marge * levier
- crossMode dans le body si détecté (évite 330005)
- positionSide auto si hedge (évite 330011)
- quantification prix au tick (BUY=floor, SELL=ceil)
"""

import time, hmac, base64, hashlib, json, math
from typing import Literal, Optional, Tuple, Dict, Any

import httpx
from config import SETTINGS
from logger_utils import get_logger

log = get_logger("kucoin.trader")

BASE = SETTINGS.kucoin_base_url.rstrip("/")

_SERVER_OFFSET = 0.0

def _sync_server_time(client: httpx.Client):
    global _SERVER_OFFSET
    try:
        r = client.get(BASE + "/api/v1/timestamp", timeout=5.0)
        if r.status_code == 200:
            server_ms = int((r.json() or {}).get("data", 0))
            _SERVER_OFFSET = (server_ms / 1000.0) - time.time()
            log.info(f"time sync offset={_SERVER_OFFSET:.3f}s")
    except Exception as e:
        log.warning(f"time sync failed: {e}")

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

# -------- helpers meta / tick --------
def _contract_meta(c: httpx.Client, symbol_api: str) -> Dict[str, Any]:
    try:
        r = c.get(f"{BASE}/api/v1/contracts/{symbol_api}", headers=_headers("GET", f"/api/v1/contracts/{symbol_api}"))
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
    if tick <= 0: return price
    steps = price / tick
    qsteps = math.floor(steps + 1e-12) if side.lower() == "buy" else math.ceil(steps - 1e-12)
    return float(qsteps) * tick

# -------- position & margin modes --------
def _get_position(c: httpx.Client, symbol_api: str) -> Dict[str, Any]:
    path = f"/api/v1/position?symbol={symbol_api}"
    try:
        r = c.get(BASE + path, headers=_headers("GET", path))
        if r.status_code == 200:
            data = (r.json() or {}).get("data")
            if isinstance(data, dict): return data
            if isinstance(data, list) and data: return data[0]
    except Exception:
        pass
    return {}

def _detect_cross_mode(pos: Dict[str, Any]) -> Optional[bool]:
    # True=cross, False=isolated, None inconnue
    cm = pos.get("crossMode")
    if cm is None: return None
    try: return bool(cm)
    except Exception: return None

def _detect_position_mode(pos: Dict[str, Any]) -> str:
    # 'hedge' ou 'oneway'
    for k in ("positionMode", "posMode", "mode"):
        v = pos.get(k)
        if isinstance(v, str):
            v = v.lower()
            if "hedge" in v: return "hedge"
            if "one" in v or "single" in v: return "oneway"
    long_keys = ("longQty","longSize","longOpen","longAvailable")
    short_keys = ("shortQty","shortSize","shortOpen","shortAvailable")
    if any(k in pos for k in long_keys) and any(k in pos for k in short_keys):
        return "hedge"
    return "oneway"

class KucoinTrader:
    def __init__(self):
        self.client = httpx.Client(timeout=10.0)
        self.margin_per_trade = float(getattr(SETTINGS, "margin_per_trade", 20.0))
        self.default_leverage = int(getattr(SETTINGS, "default_leverage", 10))
        _sync_server_time(self.client)

    def _value_qty(self) -> float:
        return float(self.margin_per_trade) * float(self.default_leverage)

    # ------------------ ORDERS ------------------
    def place_limit(
        self,
        symbol: str,                # ex: HYPEUSDTM
        side: Literal["buy","sell"],
        price: float,
        post_only: bool = True
    ) -> Tuple[bool, Dict[str, Any]]:
        meta = _contract_meta(self.client, symbol)
        tick = _safe_tick(meta)
        qprice = _quantize(float(price), tick, side)

        pos = _get_position(self.client, symbol)
        cross_mode = _detect_cross_mode(pos)          # True=cross / False=isolated
        pos_mode = _detect_position_mode(pos)         # 'hedge' / 'oneway'
        include_ps = (pos_mode == "hedge")

        log.info("[marginMode] %s -> %s", symbol, ("cross" if cross_mode else "isolated"))
        log.info("[positionMode] %s -> %s (include positionSide=%s)", symbol, pos_mode, include_ps)
        log.info("[place_limit] %s %s px=%s (tick=%.8f) valueQty=%.2f postOnly=%s",
                 symbol, side, f"{qprice:.12f}", tick, self._value_qty(), post_only)

        def _body(lev: str, include_position_side: bool, include_cross_mode: bool) -> Dict[str, Any]:
            b = {
                "clientOid": str(_now_ms()),
                "symbol": symbol,
                "type": "limit",
                "side": side.lower(),
                "price": f"{qprice:.12f}",
                "valueQty": f"{self._value_qty():.2f}",
                "leverage": lev,
                "timeInForce": "GTC",
                "postOnly": bool(post_only),
                "reduceOnly": False,
            }
            if include_position_side:
                b["positionSide"] = "long" if side.lower() == "buy" else "short"
            # 👉 comme avant : on envoie crossMode si on le connaît
            if include_cross_mode and cross_mode is not None:
                b["crossMode"] = bool(cross_mode)
            return b

        def _send(b: Dict[str, Any]) -> Tuple[bool, Dict[str, Any]]:
            path = "/api/v1/orders"
            body_json = json.dumps(b, separators=(",", ":"))
            r = self.client.post(BASE + path, headers=_headers("POST", path, body_json), content=body_json)
            ok_http = (r.status_code == 200)
            try:
                js = r.json() if (r.content and r.text) else {}
            except Exception:
                js = {}
            code = (js or {}).get("code")
            ok = bool(ok_http and code == "200000")
            log.info("[kucoin REST] HTTP=%s code=%s msg=%s", r.status_code, code, (js or {}).get("msg"))
            return ok, js

        # Tentative 1 — comme avant : crossMode si dispo, positionSide si hedge
        ok, js = _send(_body(str(self.default_leverage), include_ps, True))
        code = (js or {}).get("code") or ""
        msg  = (js or {}).get("msg") or ""

        # Retry levier invalide
        if (not ok) and (code == "100001" or "Leverage parameter invalid" in msg):
            lev_fb = "5" if str(self.default_leverage) != "5" else "3"
            log.info("[leverage] retry %s with leverage=%s", symbol, lev_fb)
            ok, js = _send(_body(lev_fb, include_ps, True))
            code = (js or {}).get("code") or ""
            if ok and code == "200000":
                return ok, js

        # Retry mismatch position mode — inverse présence de positionSide
        if (not ok) and code == "330011":
            alternate_ps = not include_ps
            log.info("[positionMode] retry %s with include positionSide=%s", symbol, alternate_ps)
            ok, js = _send(_body(str(self.default_leverage), alternate_ps, True))
            return ok, js

        # Retry margin mode mismatch — si crossMode manquait côté serveur
        if (not ok) and code == "330005":
            # forcer explicitement crossMode (si None, on tente False->isolated)
            force_cross = cross_mode if cross_mode is not None else False
            log.info("[marginMode] retry %s with crossMode=%s", symbol, force_cross)
            ok, js = _send(_body(str(self.default_leverage), include_ps, True))
            return ok, js

        return ok, js

    def place_market(
        self,
        symbol: str,
        side: Literal["buy","sell"]
    ) -> Tuple[bool, Dict[str, Any]]:
        pos = _get_position(self.client, symbol)
        cross_mode = _detect_cross_mode(pos)
        pos_mode = _detect_position_mode(pos)
        include_ps = (pos_mode == "hedge")

        log.info("[positionMode] %s -> %s (include positionSide=%s)", symbol, pos_mode, include_ps)

        def _body(lev: str, include_position_side: bool) -> Dict[str, Any]:
            b = {
                "clientOid": str(_now_ms()),
                "symbol": symbol,
                "type": "market",
                "side": side.lower(),
                "valueQty": f"{self._value_qty():.2f}",
                "leverage": lev,
                "reduceOnly": False,
            }
            if include_position_side:
                b["positionSide"] = "long" if side.lower() == "buy" else "short"
            if cross_mode is not None:
                b["crossMode"] = bool(cross_mode)
            return b

        def _send(b: Dict[str, Any]) -> Tuple[bool, Dict[str, Any]]:
            path = "/api/v1/orders"
            body_json = json.dumps(b, separators=(",", ":"))
            r = self.client.post(BASE + path, headers=_headers("POST", path, body_json), content=body_json)
            ok_http = (r.status_code == 200)
            try:
                js = r.json() if (r.content and r.text) else {}
            except Exception:
                js = {}
            code = (js or {}).get("code")
            ok = bool(ok_http and code == "200000")
            log.info("[kucoin REST] HTTP=%s code=%s msg=%s", r.status_code, code, (js or {}).get("msg"))
            return ok, js

        ok, js = _send(_body(str(self.default_leverage), include_ps))
        code = (js or {}).get("code") or ""
        msg  = (js or {}).get("msg") or ""

        if (not ok) and (code == "100001" or "Leverage parameter invalid" in msg):
            lev_fb = "5" if str(self.default_leverage) != "5" else "3"
            log.info("[leverage] retry %s with leverage=%s", symbol, lev_fb)
            ok, js = _send(_body(lev_fb, include_ps))
            if ok and (js or {}).get("code") == "200000":
                return ok, js

        if (not ok) and code == "330011":
            alternate_ps = not include_ps
            log.info("[positionMode] retry %s with include positionSide=%s", symbol, alternate_ps)
            ok, js = _send(_body(str(self.default_leverage), alternate_ps))
            return ok, js

        return ok, js

    def close_reduce_market(
        self,
        symbol: str,
        side: Literal["buy","sell"],
        value_qty: float
    ) -> Tuple[bool, Dict[str, Any]]:
        pos = _get_position(self.client, symbol)
        cross_mode = _detect_cross_mode(pos)
        pos_mode = _detect_position_mode(pos)
        include_ps = (pos_mode == "hedge")

        def _body(include_position_side: bool) -> Dict[str, Any]:
            b = {
                "clientOid": str(_now_ms()),
                "symbol": symbol,
                "type": "market",
                "side": side.lower(),
                "reduceOnly": True,
                "valueQty": f"{value_qty:.2f}",
            }
            if include_position_side:
                b["positionSide"] = "long" if side.lower() == "buy" else "short"
            if cross_mode is not None:
                b["crossMode"] = bool(cross_mode)
            return b

        def _send(b: Dict[str, Any]) -> Tuple[bool, Dict[str, Any]]:
            path = "/api/v1/orders"
            body_json = json.dumps(b, separators=(",", ":"))
            r = self.client.post(BASE + path, headers=_headers("POST", path, body_json), content=body_json)
            ok_http = (r.status_code == 200)
            try:
                js = r.json() if (r.content and r.text) else {}
            except Exception:
                js = {}
            code = (js or {}).get("code")
            ok = bool(ok_http and code == "200000")
            log.info("[kucoin REST] HTTP=%s code=%s msg=%s", r.status_code, code, (js or {}).get("msg"))
            return ok, js

        ok, js = _send(_body(include_ps))
        if (not ok) and (js or {}).get("code") == "330011":
            ok, js = _send(_body(not include_ps))
        return ok, js
