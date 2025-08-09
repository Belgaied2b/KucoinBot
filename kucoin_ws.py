import time, hmac, base64, hashlib, httpx, ujson as json, asyncio, websockets
from typing import Callable, Dict, Any
from config import SETTINGS
from logger_utils import get_logger

TOKEN_URL="/api/v1/bullet-private"
log = get_logger("kucoin.ws")

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

class KucoinPrivateWS:
    def __init__(self):
        self.base=SETTINGS.kucoin_base_url
        self.key=SETTINGS.kucoin_key; self.secret=SETTINGS.kucoin_secret; self.passphrase=SETTINGS.kucoin_passphrase
        self.token=None; self.endpoint=None
        self.listeners={"fill":[], "position":[], "order":[]}
        _sync_server_time()

    def _headers(self, method: str, path: str, body: str = ""):
        ts = int((time.time() + _SERVER_OFFSET)*1000)
        now=str(ts)
        sig=base64.b64encode(hmac.new(self.secret.encode(), (now+method+path+body).encode(), hashlib.sha256).digest()).decode()
        psp=base64.b64encode(hmac.new(self.secret.encode(), self.passphrase.encode(), hashlib.sha256).digest()).decode()
        return {"KC-API-KEY": self.key,"KC-API-SIGN": sig,"KC-API-TIMESTAMP": now,"KC-API-PASSPHRASE": psp,"KC-API-KEY-VERSION":"2","Content-Type":"application/json"}

    def on(self, event: str, cb: Callable[[Dict[str,Any]], None]): self.listeners[event].append(cb)
    def _emit(self, event: str, payload: Dict[str,Any]):
        for cb in self.listeners.get(event,[]): 
            try: cb(payload)
            except Exception as e: log.warning(f"listener error: {e}")

    def _post(self, path, body=None):
        with httpx.Client(timeout=10.0) as c:
            r=c.post(self.base+path, headers=self._headers("POST", path, json.dumps(body) if body else ""), json=body)
            if r.status_code >= 400:
                log.error(f"bullet-private error {r.status_code}: {r.text[:200]}")
                r.raise_for_status()
            return r.json()

    async def _ensure_token(self):
        if self.token and self.endpoint: return
        data=self._post(TOKEN_URL); self.token=data["data"]["token"]
        self.endpoint=data["data"]["instanceServers"][0]["endpoint"]
        log.info("bullet-private token acquired")

    async def run(self):
        await self._ensure_token()
        ws_url=f"{self.endpoint}?token={self.token}&acceptUserMessage=true"
        while True:
            try:
                log.info("connecting private WS...")
                async with websockets.connect(ws_url, ping_interval=15, ping_timeout=15) as ws:
                    subs=[
                        {"type":"subscribe","topic":"/contractMarket/tradeOrders","privateChannel":True,"response":True},
                        {"type":"subscribe","topic":"/contract/position","privateChannel":True,"response":True},
                    ]
                    for s in subs: await ws.send(json.dumps(s))
                    log.info("subscriptions sent")
                    async for raw in ws:
                        msg=json.loads(raw)
                        if msg.get("type")=="message":
                            topic=msg.get("topic",""); data=msg.get("data",{})
                            if topic.endswith("tradeOrders"):
                                self._emit("order", data)
                                if data.get("status") in ("match","filled","partialFilled"):
                                    self._emit("fill", data)
                            if topic.endswith("position"):
                                self._emit("position", data)
            except Exception as e:
                log.warning(f"ws loop error: {e}")
                await asyncio.sleep(1.0)
                try:
                    await self._ensure_token()
                except Exception as e2:
                    log.error(f"ensure_token failed: {e2}")
                    await asyncio.sleep(3.0)
                continue
