import time, hmac, base64, hashlib, httpx, json
from typing import Literal, Optional
from config import SETTINGS
from logger_utils import get_logger

log = get_logger("kucoin.trader")

_SERVER_OFFSET = 0.0
def _sync_server_time():
    global _SERVER_OFFSET
    try:
        r = httpx.get(SETTINGS.kucoin_base_url + "/api/v1/timestamp", timeout=5.0)
        if r.status_code == 200:
            server_ms = int(r.json().get("data", 0))
            _SERVER_OFFSET = (server_ms/1000.0) - time.time()
            log.info(f"time sync offset={_SERVER_OFFSET:.3f}s")
    except Exception as e:
        log.warning(f"time sync failed: {e}")

class KucoinTrader:
    def __init__(self):
        self.base=SETTINGS.kucoin_base_url
        self.key=SETTINGS.kucoin_key; self.secret=SETTINGS.kucoin_secret; self.passphrase=SETTINGS.kucoin_passphrase
        self.client=httpx.Client(timeout=10.0)
        _sync_server_time()

    def _headers(self, method: str, path: str, body: str = ""):
        ts = int((time.time() + _SERVER_OFFSET)*1000)
        now=str(ts)
        sig=base64.b64encode(hmac.new(self.secret.encode(), (now+method+path+body).encode(), hashlib.sha256).digest()).decode()
        psp=base64.b64encode(hmac.new(self.secret.encode(), self.passphrase.encode(), hashlib.sha256).digest()).decode()
        return {"KC-API-KEY": self.key, "KC-API-SIGN": sig, "KC-API-TIMESTAMP": now,
                "KC-API-PASSPHRASE": psp, "KC-API-KEY-VERSION":"2", "Content-Type":"application/json"}

    def _post(self, path: str, body: dict):
        try:
            r=self.client.post(self.base+path, headers=self._headers("POST", path, json.dumps(body)), json=body)
            ok=r.status_code in (200,201)
            if not ok:
                log.error(f"POST {path} {r.status_code}: {r.text[:200]}")
            else:
                log.debug(f"POST {path} ok: {r.text[:120]}")
            return ok,(r.json() if r.content else {})
        except Exception as e:
            log.exception(f"POST {path} exception: {e}")
            return False, {}

    def _delete(self, path: str):
        try:
            r=self.client.delete(self.base+path, headers=self._headers("DELETE", path))
            ok=r.status_code in (200,204)
            if not ok:
                log.error(f"DELETE {path} {r.status_code}: {getattr(r,'text','')[:200]}")
            else:
                log.debug(f"DELETE {path} ok")
            return ok,(r.json() if getattr(r,'content',None) else {})
        except Exception as e:
            log.exception(f"DELETE {path} exception: {e}")
            return False, {}

    def _get(self, path: str):
        try:
            r=self.client.get(self.base+path, headers=self._headers("GET", path))
            if r.status_code==200:
                return True, r.json()
            log.error(f"GET {path} {r.status_code}: {r.text[:200]}")
            return False, {}
        except Exception as e:
            log.exception(f"GET {path} exception: {e}")
            return False, {}

    def place_limit(self, symbol: str, side: Literal["buy","sell"], price: float, client_oid: str, post_only: bool=False):
    body={"clientOid":client_oid,"symbol":symbol,"type":"limit","side":side,
          "price":f"{price:.8f}","valueQty":f"{SETTINGS.margin_per_trade:.2f}",
          "leverage":"10",
          "timeInForce":"GTC","reduceOnly":False,"postOnly":bool(post_only)}
    return self._post("/api/v1/orders", body)

    def place_limit_ioc(self, symbol: str, side: Literal["buy","sell"], price: float):
    body={"clientOid":str(int(time.time()*1000)),"symbol":symbol,"type":"limit","side":side,
          "price":f"{price:.8f}","valueQty":f"{SETTINGS.margin_per_trade:.2f}",
          "leverage":"10",
          "timeInForce":"IOC","reduceOnly":False,"postOnly":False}
    return self._post("/api/v1/orders", body)

    def place_market(self, symbol: str, side: Literal["buy","sell"]):
    body={"clientOid":str(int(time.time()*1000)),"symbol":symbol,"type":"market","side":side,
          "reduceOnly":False,"valueQty":f"{SETTINGS.margin_per_trade:.2f}",
          "leverage":"10"}
    return self._post("/api/v1/orders", body)

    def close_reduce_market(self, symbol: str, side: Literal["buy","sell"], value_qty: float):
        body={"clientOid":str(int(time.time()*1000)),"symbol":symbol,"type":"market","side":side,
              "reduceOnly":True,"valueQty":f"{value_qty:.2f}"}
        return self._post("/api/v1/orders", body)

    def cancel(self, order_id: str):
        return self._delete(f"/api/v1/orders/{order_id}")

    def cancel_by_client_oid(self, client_oid: str):
        return self._delete(f"/api/v1/order/cancelClientOrder?clientOid={client_oid}")

    def get_order_by_client_oid(self, client_oid: str):
        ok, js = self._get(f"/api/v1/order/client-order/{client_oid}")
        return js.get("data") if ok else None
