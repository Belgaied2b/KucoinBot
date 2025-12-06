# ================================================================
# bitget_client.py — Async Bitget REST Client (Futures USDT-M)
# ================================================================
import time
import hmac
import base64
import hashlib
import aiohttp
import asyncio
from typing import Any, Dict, Optional


class BitgetClient:
    """
    REST client pour Bitget Futures USDT-M (async).
    Fournit :
        - GET / POST / DELETE signés
        - klines
        - info contrats
        - positions
        - open interest
        - funding rate
    """

    BASE = "https://api.bitget.com"

    def __init__(self, api_key: str, api_secret: str, api_passphrase: str):
        self.api_key = api_key
        self.api_secret = api_secret.encode()
        self.api_passphrase = api_passphrase
        self.session: Optional[aiohttp.ClientSession] = None

    async def _ensure_session(self):
        if self.session is None or self.session.closed:
            self.session = aiohttp.ClientSession()

    # ------------------------------------------------------------
    # Signature Bitget
    # ------------------------------------------------------------
    def _sign(self, timestamp: str, method: str, path: str, body: str = "") -> str:
        """Retourne signature HMAC SHA256 base64."""
        msg = f"{timestamp}{method}{path}{body}"
        mac = hmac.new(self.api_secret, msg.encode(), hashlib.sha256).digest()
        return base64.b64encode(mac).decode()

    # ------------------------------------------------------------
    # HTTP request signée
    # ------------------------------------------------------------
    async def _request(
        self,
        method: str,
        path: str,
        params: Optional[Dict[str, Any]] = None,
        data: Optional[Dict[str, Any]] = None,
        auth: bool = True,
    ) -> Dict[str, Any]:

        await self._ensure_session()

        url = self.BASE + path
        if params is None:
            params = {}

        body = ""
        if data:
            import json
            body = json.dumps(data, separators=(",", ":"))

        headers = {}
        timestamp = str(int(time.time() * 1000))

        if auth:
            sign = self._sign(timestamp, method.upper(), path, body)
            headers.update({
                "ACCESS-KEY": self.api_key,
                "ACCESS-SIGN": sign,
                "ACCESS-TIMESTAMP": timestamp,
                "ACCESS-PASSPHRASE": self.api_passphrase,
                "Content-Type": "application/json",
            })

        try:
            async with self.session.request(
                method.upper(),
                url,
                params=params,
                data=body if data else None,
                headers=headers,
                timeout=20,
            ) as resp:
                txt = await resp.text()
                if resp.status != 200:
                    return {"code": resp.status, "msg": txt}
                import json
                return json.loads(txt)
        except Exception as e:
            return {"code": -1, "msg": str(e)}

    # ------------------------------------------------------------
    # Public Market Data
    # ------------------------------------------------------------
    async def get_klines(
        self,
        symbol: str,
        granularity: str = "1h",
        limit: int = 200
    ) -> Optional[list]:
        """
        Retourne OHLCV sous forme de liste :
        [
          [timestamp, open, high, low, close, volume]
        ]
        """
        path = "/api/mix/v1/market/candles"
        params = {
            "symbol": symbol,
            "granularity": granularity,
            "limit": limit
        }
        r = await self._request("GET", path, params=params, auth=False)
        return r.get("data")

    async def get_contract(self, symbol: str):
        """Infos du contrat : multiplier, tickSize, lotSize..."""
        path = "/api/mix/v1/market/contracts"
        r = await self._request("GET", path, auth=False)
        for c in r.get("data", []):
            if c.get("symbol") == symbol:
                return c
        return None

    # ------------------------------------------------------------
    # Private Futures — Positions
    # ------------------------------------------------------------
    async def get_position(self, symbol: str) -> Dict[str, Any]:
        path = "/api/mix/v1/position/singlePosition"
        params = {
            "symbol": symbol,
            "marginCoin": "USDT"
        }
        r = await self._request("GET", path, params=params)
        return r.get("data", {}) or {}

    # ------------------------------------------------------------
    # Institutional Metrics
    # ------------------------------------------------------------
    async def get_open_interest(self, symbol: str) -> Optional[float]:
        path = "/api/mix/v1/market/openInterest"
        params = {"symbol": symbol}
        r = await self._request("GET", path, params=params, auth=False)
        try:
            return float(r.get("data", {}).get("openInterest", 0))
        except Exception:
            return None

    async def get_funding_rate(self, symbol: str) -> Optional[float]:
        path = "/api/mix/v1/market/fundingRate"
        params = {"symbol": symbol}
        r = await self._request("GET", path, params=params, auth=False)
        try:
            return float(r.get("data", {}).get("rate", 0))
        except Exception:
            return None

    # ------------------------------------------------------------
    # Cleanup
    # ------------------------------------------------------------
    async def close(self):
        if self.session and not self.session.closed:
            await self.session.close()


# Factory pour usage externe
_client_cache: Optional[BitgetClient] = None

async def get_client(api_key: str, api_secret: str, api_passphrase: str) -> BitgetClient:
    global _client_cache
    if _client_cache is None:
        _client_cache = BitgetClient(api_key, api_secret, api_passphrase)
    return _client_cache
