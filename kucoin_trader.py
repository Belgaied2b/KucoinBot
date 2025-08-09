import time, hmac, base64, hashlib, httpx, json
from typing import Literal, Optional
from config import SETTINGS

class KucoinTrader:
    def __init__(self):
        self.base=SETTINGS.kucoin_base_url
        self.key=SETTINGS.kucoin_key; self.secret=SETTINGS.kucoin_secret; self.passphrase=SETTINGS.kucoin_passphrase
        self.client=httpx.Client(timeout=10.0)

    def _headers(self, method: str, path: str, body: str = ""):
        now=str(int(time.time()*1000))
        sig=base64.b64encode(hmac.new(self.secret.encode(), (now+method+path+body).encode(), hashlib.sha256).digest()).decode()
        psp=base64.b64encode(hmac.new(self.secret.encode(), self.passphrase.encode(), hashlib.sha256).digest()).decode()
        return {"KC-API-KEY": self.key, "KC-API-SIGN": sig, "KC-API-TIMESTAMP": now,
                "KC-API-PASSPHRASE": psp, "KC-API-KEY-VERSION":"2", "Content-Type":"application/json"}

    def place_limit(self, symbol: str, side: Literal["buy","sell"], price: float, client_oid: str, post_only: bool=False):
        body={"clientOid":client_oid,"symbol":symbol,"type":"limit","side":side,
              "price":f"{price:.8f}","valueQty":f"{SETTINGS.margin_per_trade:.2f}",
              "timeInForce":"GTC","reduceOnly":False,"postOnly":bool(post_only)}
        path="/api/v1/orders"
        r=self.client.post(self.base+path, headers=self._headers("POST", path, json.dumps(body)), json=body)
        ok=r.status_code in (200,201)
        return ok,(r.json() if r.content else {})

    def place_limit_ioc(self, symbol: str, side: Literal["buy","sell"], price: float):
        body={"clientOid":str(int(time.time()*1000)),"symbol":symbol,"type":"limit","side":side,
              "price":f"{price:.8f}","valueQty":f"{SETTINGS.margin_per_trade:.2f}",
              "timeInForce":"IOC","reduceOnly":False,"postOnly":False}
        path="/api/v1/orders"
        r=self.client.post(self.base+path, headers=self._headers("POST", path, json.dumps(body)), json=body)
        ok=r.status_code in (200,201)
        return ok,(r.json() if r.content else {})

    def place_market(self, symbol: str, side: Literal["buy","sell"]):
        body={"clientOid":str(int(time.time()*1000)),"symbol":symbol,"type":"market","side":side,
              "reduceOnly":False,"valueQty":f"{SETTINGS.margin_per_trade:.2f}"}
        path="/api/v1/orders"
        r=self.client.post(self.base+path, headers=self._headers("POST", path, json.dumps(body)), json=body)
        ok=r.status_code in (200,201)
        return ok,(r.json() if r.content else {})

    def close_reduce_market(self, symbol: str, side: Literal["buy","sell"], value_qty: float):
        body={"clientOid":str(int(time.time()*1000)),"symbol":symbol,"type":"market","side":side,
              "reduceOnly":True,"valueQty":f"{value_qty:.2f}"}
        path="/api/v1/orders"
        r=self.client.post(self.base+path, headers=self._headers("POST", path, json.dumps(body)), json=body)
        ok=r.status_code in (200,201)
        return ok,(r.json() if r.content else {})

    def cancel(self, order_id: str):
        path=f"/api/v1/orders/{order_id}"
        r=self.client.delete(self.base+path, headers=self._headers("DELETE", path))
        ok=r.status_code in (200,204)
        return ok,(r.json() if r.content else {})

    def cancel_by_client_oid(self, client_oid: str):
        path=f"/api/v1/order/cancelClientOrder?clientOid={client_oid}"
        r=self.client.delete(self.base+path, headers=self._headers("DELETE", path))
        ok=r.status_code in (200,204)
        return ok,(r.json() if r.content else {})

    def get_order_by_client_oid(self, client_oid: str) -> Optional[dict]:
        path=f"/api/v1/order/client-order/{client_oid}"
        r=self.client.get(self.base+path, headers=self._headers("GET", path))
        if r.status_code==200:
            return r.json().get("data")
        return None
