# -*- coding: utf-8 -*-
"""
kucoin_trader.py — Client Futures minimal
- Réponses NORMALISÉES (dict) -> jamais de tuples
- Sync horloge serveur (offset) et headers v2
- Helpers LIMIT / LIMIT IOC / MARKET / CLOSE reduce
"""

import time, hmac, base64, hashlib, httpx, json
from typing import Literal, Optional, Dict, Any
from config import SETTINGS
from logger_utils import get_logger

log = get_logger("kucoin.trader")

# -------------------------------------------------
# Horloge serveur (pour KC-API-TIMESTAMP)
# -------------------------------------------------
_SERVER_OFFSET = 0.0

def _sync_server_time():
    global _SERVER_OFFSET
    try:
        r = httpx.get(SETTINGS.kucoin_base_url + "/api/v1/timestamp", timeout=5.0)
        if r.status_code == 200:
            server_ms = int((r.json() or {}).get("data", 0))
            _SERVER_OFFSET = (server_ms / 1000.0) - time.time()
            log.info("time sync offset=%.3fs", _SERVER_OFFSET)
        else:
            log.warning("time sync HTTP=%s: %s", r.status_code, r.text[:160])
    except Exception as e:
        log.warning("time sync failed: %s", e)

def _now_ms() -> int:
    return int((time.time() + _SERVER_OFFSET) * 1000)

# -------------------------------------------------
# Normalisation des réponses
# -------------------------------------------------
def _normalize_response(resp: httpx.Response) -> Dict[str, Any]:
    """
    KuCoin Futures retourne typiquement:
    { "code": "200000", "data": {...} }
    On renvoie un dict homogène :
      - ok: bool
      - status: int (HTTP)
      - code: str (code API KuCoin)
      - data: dict
      - orderId: str si présent dans data
      - raw: extrait brut si non JSON
    """
    out: Dict[str, Any] = {"ok": False, "status": resp.status_code}
    try:
        js = resp.json() if resp.content else {}
    except Exception:
        out["raw"] = resp.text[:500]
        return out

    code = str(js.get("code", "")).strip()
    data = js.get("data", {}) if isinstance(js.get("data"), dict) else {}

    out["code"] = code or ""
    out["data"] = data
    out["raw"]  = js

    # Règle standard KuCoin: 200000 => OK
    out["ok"] = (resp.status_code in (200, 201)) and (code in ("", "200000"))
    if "orderId" in data:
        out["orderId"] = data.get("orderId")

    return out

def _error_dict(msg: str, status: int = 0) -> Dict[str, Any]:
    return {"ok": False, "status": status, "error": msg}

# -------------------------------------------------
# Client
# -------------------------------------------------
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

    # ------------------ SIGNATURE ------------------
    def _headers(self, method: str, path: str, body: str = "") -> Dict[str, str]:
        ts = str(_now_ms())
        payload = ts + method + path + body
        sig = base64.b64encode(
            hmac.new(self.secret.encode(), payload.encode(), hashlib.sha256).digest()
        ).decode()
        psp = base64.b64encode(
            hmac.new(self.secret.encode(), self.passphrase.encode(), hashlib.sha256).digest()
        ).decode()
        return {
            "KC-API-KEY": self.key,
            "KC-API-SIGN": sig,
            "KC-API-TIMESTAMP": ts,
            "KC-API-PASSPHRASE": psp,
            "KC-API-KEY-VERSION": "2",
            "Content-Type": "application/json",
        }

    # ------------------ HTTP wrappers ------------------
    def _post(self, path: str, body: dict) -> Dict[str, Any]:
        try:
            body_json = json.dumps(body, separators=(",", ":"))
            url = self.base + path
            resp = self.client.post(url, headers=self._headers("POST", path, body_json), content=body_json)
            out = _normalize_response(resp)

            # Si timestamp invalide -> resync et 2e tentative
            if (not out["ok"]) and resp.status_code in (401, 429) and "timestamp" in (resp.text or "").lower():
                _sync_server_time()
                resp2 = self.client.post(url, headers=self._headers("POST", path, body_json), content=body_json)
                out = _normalize_response(resp2)

            if not out["ok"]:
                log.error("POST %s %s: %s", path, out.get("status"), str(out.get("raw"))[:240])
            else:
                log.debug("POST %s ok: %s", path, str(out.get("raw"))[:200])
            return out
        except Exception as e:
            log.exception("POST %s exception: %s", path, e)
            return _error_dict(f"POST exception: {e}")

    def _delete(self, path: str) -> Dict[str, Any]:
        try:
            url = self.base + path
            resp = self.client.delete(url, headers=self._headers("DELETE", path))
            out = _normalize_response(resp)
            if not out["ok"]:
                log.error("DELETE %s %s: %s", path, out.get("status"), str(out.get("raw"))[:240])
            else:
                log.debug("DELETE %s ok", path)
            return out
        except Exception as e:
            log.exception("DELETE %s exception: %s", path, e)
            return _error_dict(f"DELETE exception: {e}")

    def _get(self, path: str) -> Dict[str, Any]:
        try:
            url = self.base + path
            resp = self.client.get(url, headers=self._headers("GET", path))
            out = _normalize_response(resp)
            if not out["ok"]:
                log.error("GET %s %s: %s", path, out.get("status"), str(out.get("raw"))[:240])
            return out
        except Exception as e:
            log.exception("GET %s exception: %s", path, e)
            return _error_dict(f"GET exception: {e}")

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
        client_oid: str,
        post_only: bool = False
    ) -> Dict[str, Any]:
        body = {
            "clientOid": client_oid,
            "symbol": symbol,
            "type": "limit",
            "side": side,
            "price": f"{price:.8f}",
            "valueQty": f"{self._value_qty():.2f}",      # ex: 200.00 si 20 * 10
            "leverage": str(self.default_leverage),
            "timeInForce": "GTC",
            "reduceOnly": False,
            "postOnly": bool(post_only),
        }
        log.info("[place_limit] %s %s px=%s valueQty=%s lev=%s postOnly=%s",
                 symbol, side, body["price"], body["valueQty"], self.default_leverage, post_only)
        return self._post("/api/v1/orders", body)

    def place_limit_ioc(
        self,
        symbol: str,
        side: Literal["buy", "sell"],
        price: float
    ) -> Dict[str, Any]:
        body = {
            "clientOid": str(_now_ms()),
            "symbol": symbol,
            "type": "limit",
            "side": side,
            "price": f"{price:.8f}",
            "valueQty": f"{self._value_qty():.2f}",
            "leverage": str(self.default_leverage),
            "timeInForce": "IOC",
            "reduceOnly": False,
            "postOnly": False,
        }
        log.info("[place_limit_ioc] %s %s px=%s valueQty=%s lev=%s",
                 symbol, side, body["price"], body["valueQty"], self.default_leverage)
        return self._post("/api/v1/orders", body)

    def place_market(
        self,
        symbol: str,
        side: Literal["buy", "sell"]
    ) -> Dict[str, Any]:
        body = {
            "clientOid": str(_now_ms()),
            "symbol": symbol,
            "type": "market",
            "side": side,
            "reduceOnly": False,
            "valueQty": f"{self._value_qty():.2f}",
            "leverage": str(self.default_leverage),
        }
        log.info("[place_market] %s %s valueQty=%s lev=%s",
                 symbol, side, body["valueQty"], self.default_leverage)
        return self._post("/api/v1/orders", body)

    def close_reduce_market(
        self,
        symbol: str,
        side: Literal["buy", "sell"],
        value_qty: float
    ) -> Dict[str, Any]:
        # sortie partielle ou totale, reduceOnly
        body = {
            "clientOid": str(_now_ms()),
            "symbol": symbol,
            "type": "market",
            "side": side,
            "reduceOnly": True,
            "valueQty": f"{value_qty:.2f}",
        }
        log.info("[close_reduce_market] %s %s valueQty=%s (reduceOnly)",
                 symbol, side, body["valueQty"])
        return self._post("/api/v1/orders", body)

    # ------------------ CANCEL / QUERY ------------------
    def cancel(self, order_id: str) -> Dict[str, Any]:
        return self._delete(f"/api/v1/orders/{order_id}")

    def cancel_by_client_oid(self, client_oid: str) -> Dict[str, Any]:
        return self._delete(f"/api/v1/order/cancelClientOrder?clientOid={client_oid}")

    def get_order_by_client_oid(self, client_oid: str) -> Optional[Dict[str, Any]]:
        out = self._get(f"/api/v1/order/client-order/{client_oid}")
        return out.get("data") if out.get("ok") else None
