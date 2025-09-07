# -*- coding: utf-8 -*-
"""
kucoin_trader.py — Client REST KuCoin Futures (signature V2)
- Signatures correctes + sync time serveur
- leverage en string
- valueQty = marge * levier (fallback)
- logs utiles (code/msg) en INFO
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

    # ------------------ ORDERS ------------------

    def place_limit(
        self,
        symbol: str,
        side: Literal["buy", "sell"],
        price: float,
        post_only: bool = False
    ) -> Tuple[bool, Dict[str, Any]]:
        body = {
            "clientOid": str(self._now_ms()),
            "symbol": symbol,
            "type": "limit",
            "side": side,
            "price": f"{price:.8f}",
            "valueQty": f"{self._value_qty():.2f}",      # ex: 200.00 USDT si 20 * 10
            "leverage": str(self.default_leverage),      # string exigée par API
            "timeInForce": "GTC",
            "reduceOnly": False,
            "postOnly": bool(post_only),
        }
        log.info(f"[place_limit] {symbol} {side} px={body['price']} valueQty={body['valueQty']} postOnly={body['postOnly']}")
        return self._post("/api/v1/orders", body)

    def place_market(
        self,
        symbol: str,
        side: Literal["buy", "sell"]
    ) -> Tuple[bool, Dict[str, Any]]:
        body = {
            "clientOid": str(self._now_ms()),
            "symbol": symbol,
            "type": "market",
            "side": side,
            "reduceOnly": False,
            "valueQty": f"{self._value_qty():.2f}",
            "leverage": str(self.default_leverage),
        }
        log.info(f"[place_market] {symbol} {side} valueQty={body['valueQty']}")
        return self._post("/api/v1/orders", body)

    def close_reduce_market(
        self,
        symbol: str,
        side: Literal["buy", "sell"],
        value_qty: float
    ) -> Tuple[bool, Dict[str, Any]]:
        body = {
            "clientOid": str(self._now_ms()),
            "symbol": symbol,
            "type": "market",
            "side": side,
            "reduceOnly": True,
            "valueQty": f"{value_qty:.2f}",
        }
        log.info(f"[close_reduce_market] {symbol} {side} valueQty={body['valueQty']} (reduceOnly)")
        return self._post("/api/v1/orders", body)

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
