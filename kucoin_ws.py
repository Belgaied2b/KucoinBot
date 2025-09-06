# kucoin_ws.py — privé Futures WS (bullet-private) prêt à coller
import time
import hmac
import base64
import hashlib
import httpx
import ujson as json
import asyncio
import websockets
import random
from typing import Callable, Dict, Any, Optional, List
from websockets.exceptions import ConnectionClosed, ConnectionClosedError, ConnectionClosedOK

from config import SETTINGS
from logger_utils import get_logger

TOKEN_PATH = "/api/v1/bullet-private"
TIME_PATH  = "/api/v1/timestamp"

log = get_logger("kucoin.ws")

# Décalage local -> serveur (en secondes)
_SERVER_OFFSET = 0.0

def _sync_server_time() -> None:
    """Synchronise l'heure locale sur l'heure serveur KuCoin Futures (ms)."""
    global _SERVER_OFFSET
    try:
        url = SETTINGS.kucoin_base_url.rstrip("/") + TIME_PATH
        r = httpx.get(url, timeout=5.0)
        r.raise_for_status()
        server_ms = int(r.json().get("data", 0))
        _SERVER_OFFSET = (server_ms / 1000.0) - time.time()
        log.info(f"time sync offset={_SERVER_OFFSET:.3f}s")
    except Exception as e:
        log.warning(f"time sync failed: {e}")

def _ts_ms() -> int:
    return int((time.time() + _SERVER_OFFSET) * 1000)

def _b64_hmac_sha256(secret: str, payload: str) -> str:
    return base64.b64encode(
        hmac.new(secret.encode("utf-8"), payload.encode("utf-8"), hashlib.sha256).digest()
    ).decode("utf-8")

class KucoinPrivateWS:
    def __init__(self):
        self.base = SETTINGS.kucoin_base_url.rstrip("/")
        self.key = SETTINGS.kucoin_key
        self.secret = SETTINGS.kucoin_secret
        self.passphrase = SETTINGS.kucoin_passphrase

        self.token: Optional[str] = None
        self.endpoint: Optional[str] = None

        self.listeners: Dict[str, List[Callable[[Dict[str, Any]], None]]] = {
            "fill": [], "position": [], "order": []
        }

        # paramètres ping/pong fournis par bullet-private
        self.ping_interval_ms: int = 15000
        self.ping_timeout_ms: int = 10000
        self._pong_deadline: float = float("inf")

        # runtime
        self.ws: Optional[websockets.WebSocketClientProtocol] = None
        self._running: bool = False
        self._ping_task: Optional[asyncio.Task] = None
        self._recv_task: Optional[asyncio.Task] = None

    # ---------------------- Auth HTTP (v2) ----------------------
    def _headers(self, method: str, path: str, body_str: str = "") -> Dict[str, str]:
        """
        Construit les en-têtes signés v2 :
        KC-API-SIGN = base64(HMAC_SHA256(secret, timestamp + method + path + body))
        KC-API-PASSPHRASE = base64(HMAC_SHA256(secret, passphrase))
        timestamp = ms (string)
        """
        ts = str(_ts_ms())
        str_to_sign = ts + method.upper() + path + (body_str or "")
        sig = _b64_hmac_sha256(self.secret, str_to_sign)
        psp = _b64_hmac_sha256(self.secret, self.passphrase)

        return {
            "KC-API-KEY": self.key,
            "KC-API-SIGN": sig,
            "KC-API-TIMESTAMP": ts,
            "KC-API-PASSPHRASE": psp,
            "KC-API-KEY-VERSION": "2",
            "Accept": "application/json",
            "Content-Type": "application/json",
        }

    def _post_signed(self, path: str, body: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        """
        POST signé en envoyant exactement la même chaîne que celle utilisée dans la signature.
        ⚠️ N'utilise PAS le paramètre httpx `json=` pour éviter toute re-sérialisation.
        """
        url = self.base + path
        # sérialisation compacte et stable (ordre non garanti côté dict -> ok car on signe EXACTEMENT ce qu'on envoie)
        body_str = "" if body is None else json.dumps(body, separators=(",", ":"), ensure_ascii=False)
        headers = self._headers("POST", path, body_str)

        with httpx.Client(timeout=10.0) as c:
            r = c.post(url, headers=headers, content=(body_str.encode("utf-8") if body_str else None))
            if r.status_code >= 400:
                # messages d'aide si 401 signature invalide
                if r.status_code == 401:
                    log.error(
                        f"HTTP {path} 401: {r.text[:200]} — "
                        f"Vérifiez: (1) clés **Futures** actives, (2) passphrase exacte, "
                        f"(3) permission Trade, (4) horloge (offset={_SERVER_OFFSET:.3f}s)"
                    )
                else:
                    log.error(f"HTTP {path} {r.status_code}: {r.text[:200]}")
                r.raise_for_status()
            return r.json() if r.content else {}

    # ---------------------- Events ----------------------
    def on(self, event: str, cb: Callable[[Dict[str, Any]], None]) -> None:
        self.listeners.setdefault(event, []).append(cb)

    def _emit(self, event: str, payload: Dict[str, Any]) -> None:
        for cb in self.listeners.get(event, []):
            try:
                cb(payload)
            except Exception as e:
                log.warning(f"listener error on '{event}': {e}")

    # ---------------------- Token bullet-private ----------------------
    async def _ensure_token(self) -> None:
        # resync avant demande (drift max 5s côté KuCoin)
        _sync_server_time()

        # Body vide recommandé par la doc (pas nécessaire d'envoyer "{}")
        data = self._post_signed(TOKEN_PATH, None)
        d = data.get("data", {}) if isinstance(data, dict) else {}
        inst_list = d.get("instanceServers") or []
        inst = inst_list[0] if inst_list else {}

        self.token = d.get("token")
        self.endpoint = inst.get("endpoint")
        self.ping_interval_ms = int(inst.get("pingInterval", 15000))
        self.ping_timeout_ms = int(inst.get("pingTimeout", 10000))

        if not self.token or not self.endpoint:
            raise RuntimeError("bullet-private missing token/endpoint")

        log.info(f"bullet-private ok pingInterval={self.ping_interval_ms}ms pingTimeout={self.ping_timeout_ms}ms")

    # ---------------------- Subscribe ----------------------
    async def _subscribe(self) -> None:
        assert self.ws is not None
        # Private topics Futures (orders & position)
        subs = [
            {
                "id": str(_ts_ms()),
                "type": "subscribe",
                "topic": "/contractMarket/tradeOrders",
                "privateChannel": True,
                "response": True,
            },
            {
                "id": str(_ts_ms()) + ":pos",
                "type": "subscribe",
                "topic": "/contract/position",
                "privateChannel": True,
                "response": True,
            },
        ]
        for s in subs:
            await self.ws.send(json.dumps(s))
        log.info("subscriptions sent")

    # ---------------------- Ping / Recv ----------------------
    async def _ping_loop(self) -> None:
        try:
            while self._running and self.ws:
                pid = str(_ts_ms())
                await self.ws.send(json.dumps({"id": pid, "type": "ping"}))
                # deadline: timeout après l’envoi du ping
                self._pong_deadline = time.time() + (self.ping_timeout_ms / 1000.0)

                # ping un peu avant l’intervalle serveur
                sleep_s = max(1.0, (self.ping_interval_ms / 1000.0) * 0.9)
                await asyncio.sleep(sleep_s)

                if time.time() > self._pong_deadline:
                    raise TimeoutError("keepalive ping timeout (no pong)")
        except Exception as e:
            if self._running:
                log.warning(f"ping loop error: {e}")
            await self._safe_close()

    async def _recv_loop(self) -> None:
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

    async def _safe_close(self) -> None:
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
    async def run(self) -> None:
        self._running = True
        backoff = 1.0
        while self._running:
            try:
                await self._ensure_token()
                connect_id = str(_ts_ms())
                # ping/pong applicatif KuCoin -> désactiver le ping WebSocket natif
                ws_url = f"{self.endpoint}?token={self.token}&connectId={connect_id}&acceptUserMessage=true"
                log.info("connecting private WS...")
                async with websockets.connect(
                    ws_url, ping_interval=None, ping_timeout=None, max_size=2**22
                ) as ws:
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

    async def stop(self) -> None:
        self._running = False
        await self._safe_close()
