import time, hmac, base64, hashlib, httpx, ujson as json, asyncio, websockets, random
from typing import Callable, Dict, Any, Optional
from websockets.exceptions import ConnectionClosed, ConnectionClosedError, ConnectionClosedOK

from config import SETTINGS
from logger_utils import get_logger

TOKEN_URL = "/api/v1/bullet-private"
log = get_logger("kucoin.ws")

_SERVER_OFFSET = 0.0
def _sync_server_time():
    global _SERVER_OFFSET
    try:
        r = httpx.get(SETTINGS.kucoin_base_url + "/api/v1/timestamp", timeout=5.0)
        if r.status_code == 200:
            server_ms = int(r.json().get("data", 0))
            _SERVER_OFFSET = (server_ms / 1000.0) - time.time()
            log.info(f"time sync offset={_SERVER_OFFSET:.3f}s")
    except Exception as e:
        log.warning(f"time sync failed: {e}")

class KucoinPrivateWS:
    def __init__(self):
        self.base = SETTINGS.kucoin_base_url
        self.key = SETTINGS.kucoin_key
        self.secret = SETTINGS.kucoin_secret
        self.passphrase = SETTINGS.kucoin_passphrase

        self.token: Optional[str] = None
        self.endpoint: Optional[str] = None

        self.listeners: Dict[str, list[Callable[[Dict[str, Any]], None]]] = {
            "fill": [], "position": [], "order": []
        }

        # ping/pong params fournis par bullet-private
        self.ping_interval_ms: int = 15000
        self.ping_timeout_ms: int = 10000
        self._pong_deadline: float = float("inf")

        # runtime
        self.ws: Optional[websockets.WebSocketClientProtocol] = None
        self._running: bool = False
        self._ping_task: Optional[asyncio.Task] = None
        self._recv_task: Optional[asyncio.Task] = None

        _sync_server_time()

    # ---------------------- Utils auth HTTP ----------------------
    def _headers(self, method: str, path: str, body: str = ""):
        ts = int((time.time() + _SERVER_OFFSET) * 1000)
        now = str(ts)
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

    def _post(self, path: str, body: dict | None = None):
        with httpx.Client(timeout=10.0) as c:
            r = c.post(self.base + path, headers=self._headers("POST", path, json.dumps(body) if body else ""), json=body)
            if r.status_code >= 400:
                log.error(f"HTTP {path} {r.status_code}: {r.text[:200]}")
                r.raise_for_status()
            return r.json()

    # ---------------------- Events ----------------------
    def on(self, event: str, cb: Callable[[Dict[str, Any]], None]):
        self.listeners.setdefault(event, []).append(cb)

    def _emit(self, event: str, payload: Dict[str, Any]):
        for cb in self.listeners.get(event, []):
            try:
                cb(payload)
            except Exception as e:
                log.warning(f"listener error on '{event}': {e}")

    # ---------------------- Bullet token ----------------------
    async def _ensure_token(self):
        # Toujours rafraîchir pour éviter expiration silencieuse
        data = self._post(TOKEN_URL, {})
        d = data.get("data", {}) if isinstance(data, dict) else {}
        inst = (d.get("instanceServers") or [None])[0] or {}

        self.token = d.get("token")
        self.endpoint = inst.get("endpoint")
        self.ping_interval_ms = int(inst.get("pingInterval", 15000))
        self.ping_timeout_ms = int(inst.get("pingTimeout", 10000))

        if not self.token or not self.endpoint:
            raise RuntimeError("bullet-private missing token/endpoint")

        log.info(f"bullet-private ok pingInterval={self.ping_interval_ms}ms pingTimeout={self.ping_timeout_ms}ms")

    # ---------------------- Subscribe ----------------------
    async def _subscribe(self):
        assert self.ws is not None
        subs = [
            {"id": str(int(time.time()*1000)), "type": "subscribe", "topic": "/contractMarket/tradeOrders", "privateChannel": True, "response": True},
            {"id": str(int(time.time()*1000))+":pos", "type": "subscribe", "topic": "/contract/position", "privateChannel": True, "response": True},
        ]
        for s in subs:
            await self.ws.send(json.dumps(s))
        log.info("subscriptions sent")

    # ---------------------- Ping / Recv ----------------------
    async def _ping_loop(self):
        try:
            while self._running and self.ws:
                pid = str(int(time.time() * 1000))
                msg = {"id": pid, "type": "ping"}
                await self.ws.send(json.dumps(msg))
                # deadline: ping_timeout après envoi
                self._pong_deadline = time.time() + (self.ping_timeout_ms / 1000.0)

                # dormir ~90% de l'intervalle pour ping avant expiration
                sleep_s = max(1.0, (self.ping_interval_ms / 1000.0) * 0.9)
                await asyncio.sleep(sleep_s)

                if time.time() > self._pong_deadline:
                    raise TimeoutError("keepalive ping timeout (no pong)")
        except Exception as e:
            if self._running:
                log.warning(f"ping loop error: {e}")
            await self._safe_close()

    async def _recv_loop(self):
        try:
            while self._running and self.ws:
                raw = await self.ws.recv()
                try:
                    msg = json.loads(raw)
                except Exception:
                    log.debug(f"non-json frame: {str(raw)[:120]}")
                    continue

                mtype = msg.get("type")
                if mtype == "pong":
                    self._pong_deadline = float("inf")
                    continue
                if mtype in ("welcome", "ack"):
                    continue
                if mtype == "error":
                    log.warning(f"WS error: {msg}")
                    continue

                if mtype == "message":
                    topic = msg.get("topic", "")
                    data = msg.get("data", {}) or {}
                    if topic.endswith("tradeOrders"):
                        self._emit("order", data)
                        if data.get("status") in ("match", "filled", "partialFilled"):
                            self._emit("fill", data)
                    elif topic.endswith("position"):
                        self._emit("position", data)
        except (ConnectionClosed, ConnectionClosedError, ConnectionClosedOK) as e:
            if self._running:
                log.warning(f"ws closed: {e}")
        except Exception as e:
            if self._running:
                log.warning(f"ws loop error: {e}")
        await self._safe_close()

    async def _safe_close(self):
        try:
            if self.ws:
                await self.ws.close()
        except Exception:
            pass
        self.ws = None
        if self._ping_task:
            self._ping_task.cancel()
        if self._recv_task:
            self._recv_task.cancel()
        self._ping_task = None
        self._recv_task = None
        self._pong_deadline = float("inf")

    # ---------------------- Lifecycle ----------------------
    async def run(self):
        self._running = True
        backoff = 1.0
        while self._running:
            try:
                await self._ensure_token()
                connect_id = str(int(time.time() * 1000))
                # ping natif désactivé: on gère le ping applicatif KuCoin
                ws_url = f"{self.endpoint}?token={self.token}&connectId={connect_id}&acceptUserMessage=true"
                log.info("connecting private WS...")
                async with websockets.connect(ws_url, ping_interval=None, ping_timeout=None, max_size=2**22) as ws:
                    self.ws = ws
                    log.info("WS connected (private)")
                    backoff = 1.0

                    await self._subscribe()

                    self._pong_deadline = float("inf")
                    self._ping_task = asyncio.create_task(self._ping_loop())
                    self._recv_task = asyncio.create_task(self._recv_loop())

                    done, pending = await asyncio.wait(
                        {self._ping_task, self._recv_task},
                        return_when=asyncio.FIRST_COMPLETED
                    )
                    for t in pending:
                        t.cancel()
                    await self._safe_close()

            except Exception as e:
                log.warning(f"ws loop error: {e}")

            if not self._running:
                break
            # backoff exponentiel + jitter
            sleep_s = min(60.0, backoff) * (0.8 + 0.4 * random.random())
            await asyncio.sleep(sleep_s)
            backoff = min(60.0, backoff * 2.0)

    async def stop(self):
        self._running = False
        await self._safe_close()
