# -*- coding: utf-8 -*-
"""
kucoin_trader.py — Client REST KuCoin Futures (signature V2)
- Signatures correctes + sync time serveur
- leverage en string
- valueQty = marge * levier (fallback)
- logs utiles (code/msg) en INFO
- GESTION position mode (hedge vs one-way) → positionSide auto + retry 330011
"""

import time, hmac, base64, hashlib, httpx, json
from typing import Literal, Optional, Tuple, Dict, Any
from config import SETTINGS
from logger_utils import get_logger

log = get_logger("kucoin.trader")

_SERVER_OFFSET = 0.0

def _sync_server_time():
    global _SERVER_OFFSET
    try:
        r = httpx.get(SETTINGS.kucoin_base_url + "/api/v1/timestamp", timeout=5.0)
        if r.status_code == 200:
            server_ms = int((r.json() or {}).get("data", 0))
            _SERVER_OFFSET = (server_ms / 1000.0) - time.time()
            log.info(f"time sync offset={_SERVER_OFFSET:.3f}s")
        else:
            log.warning("time sync HTTP=%s body=%s", r.status_code, r.text[:200])
    except Exception as e:
        log.warning(f"time sync failed: {e}")

class KucoinTrader:
    def __init__(self):
        self.base = SETTINGS.kucoin_base_url.rstrip("/")
        self.key = SETTINGS.kucoin_key
        self.secret = SETTINGS.kucoin_secret
        self.passphrase = SETTINGS.kucoin_passphrase
        self.client = httpx.Client(timeout=10.0)
        # tailles / levier (fallbacks si non définis dans Settings)
        self.margin_per_trade = float(getattr(SETTINGS, "margin_per_trade", 20.0))
        self.default_leverage = int(getattr(SETTINGS, "default_leverage", 10))
        _sync_server_time()

    def _now_ms(self) -> int:
        return int((time.time() + _SERVER_OFFSET) * 1000)

    def _headers(self, method: str, path: str, body: str = ""):
        now = str(self._now_ms())
        sig = base64.b64encode(
            hmac.new(self.secret.encode(), (now + method + path + body).encode(), hashlib.sha256).digest()
        ).decode()
        psp = base64.b64encode(
            hmac.new(self.secret.encode(), self.passphrase.encode(), hashlib.sha256).digest()
        ).decode()
        return {
            "KC-API-KEY": self.key,
            "KC-API-SIGN": sig,
            "KC-API-TIMESTAMP": now,
            "KC-API-PASSPHRASE": psp,
            "KC-API-KEY-VERSION": "2",
            "Content-Type": "application/json",
        }

    def _ok_from_response(self, r: httpx.Response) -> Tuple[bool, Dict[str, Any]]:
        ok_http = (r.status_code == 200)
        try:
            js = r.json() if (r.content and r.text) else {}
        except Exception:
            js = {}
        code = (js or {}).get("code")
        ok_code = (code == "200000")
        ok = bool(ok_http and ok_code)
        # Log clair (succès/échec)
        log.info("[kucoin REST] HTTP=%s code=%s msg=%s", r.status_code, code, (js or {}).get("msg"))
        return ok, (js or {})

    def _post(self, path: str, body: dict) -> Tuple[bool, Dict[str, Any]]:
        try:
            body_json = json.dumps(body, separators=(",", ":"))
            r = self.client.post(
                self.base + path,
                headers=self._headers("POST", path, body_json),
                content=body_json
            )
            ok, js = self._ok_from_response(r)
            return ok, js
        except Exception as e:
            log.exception(f"POST {path} exception: {e}")
            return False, {}

    def _delete(self, path: str) -> Tuple[bool, Dict[str, Any]]:
        try:
            r = self.client.delete(self.base + path, headers=self._headers("DELETE", path))
            ok, js = self._ok_from_response(r)
            return ok, js
        except Exception as e:
            log.exception(f"DELETE {path} exception: {e}")
            return False, {}

    def _get(self, path: str) -> Tuple[bool, Dict[str, Any]]:
        try:
            r = self.client.get(self.base + path, headers=self._headers("GET", path))
            ok, js = self._ok_from_response(r)
            return ok, js
        except Exception as e:
            log.exception(f"GET {path} exception: {e}")
            return False, {}

    # -------- helpers taille --------
    def _value_qty(self) -> float:
        """valueQty envoyé à KuCoin Futures = marge * levier (ex: 20 * 10 = 200)."""
        return float(self.margin_per_trade) * float(self.default_leverage)

    # -------- position mode (hedge / one-way) --------
    def _get_position_raw(self, symbol: str) -> Dict[str, Any]:
        ok, js = self._get(f"/api/v1/position?symbol={symbol}")
        if not ok or not isinstance(js, dict):
            return {}
        data = js.get("data")
        if isinstance(data, dict):
            return data
        if isinstance(data, list) and data:
            return data[0]
        return {}

    @staticmethod
    def _infer_position_mode_from_payload(pos_json: Dict[str, Any]) -> str:
        """
        Retourne 'hedge' ou 'oneway' selon la payload position (heuristique tolérante).
        """
        if not pos_json:
            return "oneway"

        # Champs explicites éventuels
        for k in ("positionMode", "posMode", "mode"):
            v = pos_json.get(k)
            if isinstance(v, str):
                v_low = v.lower()
                if "hedge" in v_low:
                    return "hedge"
                if "one" in v_low or "single" in v_low:
                    return "oneway"

        # Indices de hedge: présence de clés long/short
        long_keys = ("longQty", "longSize", "longOpen", "longAvailable")
        short_keys = ("shortQty", "shortSize", "shortOpen", "shortAvailable")
        if any(k in pos_json for k in long_keys) and any(k in pos_json for k in short_keys):
            return "hedge"

        # Structures imbriquées
        for k in ("positions", "items", "data"):
            arr = pos_json.get(k)
            if isinstance(arr, list) and len(arr) >= 2:
                sides = {str(x.get("side", "")).lower() for x in arr if isinstance(x, dict)}
                if "long" in sides and "short" in sides:
                    return "hedge"

        return "oneway"

    @staticmethod
    def _needs_position_side(position_mode: str) -> bool:
        return str(position_mode).lower() == "hedge"

    # ------------------ ORDERS ------------------

    def place_limit(
        self,
        symbol: str,
        side: Literal["buy", "sell"],
        price: float,
        post_only: bool = False
    ) -> Tuple[bool, Dict[str, Any]]:
        """
        Ajoute positionSide en hedge, pas en one-way.
        Retry 1x si 330011 (position mode mismatch) en inversant la présence de positionSide.
        Retry 1x si 100001 (levier invalide).
        """
        client_oid = str(self._now_ms())
        s_low = side.lower()
        value_qty = f"{self._value_qty():.2f}"

        # Détecter position mode (hedge/oneway)
        pos_raw = self._get_position_raw(symbol)
        pos_mode = self._infer_position_mode_from_payload(pos_raw)
        include_ps = self._needs_position_side(pos_mode)
        log.info("[positionMode] %s -> %s (include positionSide=%s)", symbol, pos_mode, include_ps)

        def _body(leverage_str: str, include_position_side: bool) -> dict:
            b = {
                "clientOid": client_oid,
                "symbol": symbol,
                "type": "limit",
                "side": s_low,
                "price": f"{price:.8f}",
                "valueQty": value_qty,
                "leverage": leverage_str,
                "timeInForce": "GTC",
                "reduceOnly": False,
                "postOnly": bool(post_only),
            }
            if include_position_side:
                b["positionSide"] = "long" if s_low == "buy" else "short"
            return b

        def _send(b: dict) -> Tuple[bool, Dict[str, Any]]:
            log.info(
                "[place_limit] %s %s px=%s valueQty=%s postOnly=%s%s",
                symbol, b.get("side"), b.get("price"), b.get("valueQty"),
                b.get("postOnly"),
                f" positionSide={b.get('positionSide')}" if "positionSide" in b else ""
            )
            return self._post("/api/v1/orders", b)

        # Tentative #1
        ok, js = _send(_body(str(self.default_leverage), include_ps))
        code = (js or {}).get("code")
        msg  = (js or {}).get("msg") or ""

        # Retry levier invalide
        if (not ok or code != "200000") and (code == "100001" or "Leverage parameter invalid" in msg):
            lev_fb = "5" if str(self.default_leverage) != "5" else "3"
            log.info("[leverage] retry %s with leverage=%s", symbol, lev_fb)
            ok, js = _send(_body(lev_fb, include_ps))
            code = (js or {}).get("code")
            msg  = (js or {}).get("msg") or ""
            if ok and code == "200000":
                return ok, js

        # Retry mismatch position mode
        if (not ok or code != "200000") and code == "330011":
            # Re-check et inverse la présence de positionSide
            pos_raw2 = self._get_position_raw(symbol)
            pos_mode2 = self._infer_position_mode_from_payload(pos_raw2)
            include_ps2 = self._needs_position_side(pos_mode2)
            alternate = not include_ps2 if include_ps2 == include_ps else include_ps2
            log.info("[positionMode] retry %s with include positionSide=%s (detected=%s)", symbol, alternate, pos_mode2)
            ok, js = _send(_body(str(self.default_leverage), alternate))
            return ok, js

        return ok, js

    def place_market(
        self,
        symbol: str,
        side: Literal["buy", "sell"]
    ) -> Tuple[bool, Dict[str, Any]]:
        """
        Market avec positionSide en hedge. Retry 1x si 330011. Retry 1x si 100001.
        """
        client_oid = str(self._now_ms())
        s_low = side.lower()
        value_qty = f"{self._value_qty():.2f}"

        pos_raw = self._get_position_raw(symbol)
        pos_mode = self._infer_position_mode_from_payload(pos_raw)
        include_ps = self._needs_position_side(pos_mode)
        log.info("[positionMode] %s -> %s (include positionSide=%s)", symbol, pos_mode, include_ps)

        def _body(leverage_str: str, include_position_side: bool) -> dict:
            b = {
                "clientOid": client_oid,
                "symbol": symbol,
                "type": "market",
                "side": s_low,
                "reduceOnly": False,
                "valueQty": value_qty,
                "leverage": leverage_str,
            }
            if include_position_side:
                b["positionSide"] = "long" if s_low == "buy" else "short"
            return b

        def _send(b: dict) -> Tuple[bool, Dict[str, Any]]:
            log.info(
                "[place_market] %s %s valueQty=%s%s",
                symbol, b.get("side"), b.get("valueQty"),
                f" positionSide={b.get('positionSide')}" if "positionSide" in b else ""
            )
            return self._post("/api/v1/orders", b)

        ok, js = _send(_body(str(self.default_leverage), include_ps))
        code = (js or {}).get("code")
        msg  = (js or {}).get("msg") or ""

        if (not ok or code != "200000") and (code == "100001" or "Leverage parameter invalid" in msg):
            lev_fb = "5" if str(self.default_leverage) != "5" else "3"
            log.info("[leverage] retry %s with leverage=%s", symbol, lev_fb)
            ok, js = _send(_body(lev_fb, include_ps))
            code = (js or {}).get("code")
            if ok and code == "200000":
                return ok, js

        if (not ok or code != "200000") and code == "330011":
            pos_raw2 = self._get_position_raw(symbol)
            pos_mode2 = self._infer_position_mode_from_payload(pos_raw2)
            include_ps2 = self._needs_position_side(pos_mode2)
            alternate = not include_ps2 if include_ps2 == include_ps else include_ps2
            log.info("[positionMode] retry %s with include positionSide=%s (detected=%s)", symbol, alternate, pos_mode2)
            ok, js = _send(_body(str(self.default_leverage), alternate))
            return ok, js

        return ok, js

    def close_reduce_market(
        self,
        symbol: str,
        side: Literal["buy", "sell"],
        value_qty: float
    ) -> Tuple[bool, Dict[str, Any]]:
        """
        Close reduceOnly market. En hedge, précise positionSide pour fermer le bon côté.
        Retry 1x si 330011 (mismatch).
        """
        client_oid = str(self._now_ms())
        s_low = side.lower()
        value_qty_s = f"{value_qty:.2f}"

        pos_raw = self._get_position_raw(symbol)
        pos_mode = self._infer_position_mode_from_payload(pos_raw)
        include_ps = self._needs_position_side(pos_mode)
        log.info("[positionMode] %s -> %s (include positionSide=%s)", symbol, pos_mode, include_ps)

        def _body(include_position_side: bool) -> dict:
            b = {
                "clientOid": client_oid,
                "symbol": symbol,
                "type": "market",
                "side": s_low,
                "reduceOnly": True,
                "valueQty": value_qty_s,
            }
            if include_position_side:
                # même mapping buy->long / sell->short
                b["positionSide"] = "long" if s_low == "buy" else "short"
            return b

        def _send(b: dict) -> Tuple[bool, Dict[str, Any]]:
            log.info(
                "[close_reduce_market] %s %s valueQty=%s (reduceOnly)%s",
                symbol, b.get("side"), b.get("valueQty"),
                f" positionSide={b.get('positionSide')}" if "positionSide" in b else ""
            )
            return self._post("/api/v1/orders", b)

        ok, js = _send(_body(include_ps))
        code = (js or {}).get("code")

        if (not ok or code != "200000") and code == "330011":
            pos_raw2 = self._get_position_raw(symbol)
            pos_mode2 = self._infer_position_mode_from_payload(pos_raw2)
            include_ps2 = self._needs_position_side(pos_mode2)
            alternate = not include_ps2 if include_ps2 == include_ps else include_ps2
            log.info("[positionMode] retry %s with include positionSide=%s (detected=%s)", symbol, alternate, pos_mode2)
            ok, js = _send(_body(alternate))
            return ok, js

        return ok, js

    # ------------------ CANCEL / QUERY ------------------

    def cancel(self, order_id: str) -> Tuple[bool, Dict[str, Any]]:
        return self._delete(f"/api/v1/orders/{order_id}")

    def cancel_by_client_oid(self, client_oid: str) -> Tuple[bool, Dict[str, Any]]:
        return self._delete(f"/api/v1/order/cancelClientOrder?clientOid={client_oid}")

    def get_order_by_client_oid(self, client_oid: str) -> Optional[Dict[str, Any]]:
        ok, js = self._get(f"/api/v1/order/client-order/{client_oid}")
        if not ok:
            return None
        return (js or {}).get("data")
