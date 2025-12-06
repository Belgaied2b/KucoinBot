# =====================================================================
# bitget_client.py — Async Bitget REST Client (Futures USDT-M)
# Version corrigée, optimisée et 100% compatible avec scanner/analyzer
# =====================================================================

import time
import hmac
import base64
import hashlib
import aiohttp
from typing import Any, Dict, Optional


class BitgetClient:
    """
    REST client pour Bitget Futures (USDT-M).
    Fournit :
        - GET / POST / DELETE signés
        - klines
        - info contrats
        - positions
        - metrics institutionnelles (OI, funding…)
    """

    BASE = "https://api.bitget.com"

    def __init__(self, api_key: str, api_secret: str, api_passphrase: str):
        self.api_key = api_key
        self.api_secret = api_secret.encode()
        self.api_passphrase = api_passphrase
        self.session: Optional[aiohttp.ClientSession] = None

    # ------------------------------------------------------------
    async def _ensure_session(self):
        if self.session is None or self.session.closed:
            self.session = aiohttp.ClientSession()

    # ------------------------------------------------------------
    # SIGNATURE BITGET (Correction totale)
    # ------------------------------------------------------------
    def _sign(self, timestamp: str, method: str, path: str, query: str, body: str = "") -> str:
        """
        Signature officielle Bitget :
        sign = base64( HMAC_SHA256(timestamp + method + requestPath + queryString + body) )
        """
        message = f"{timestamp}{method}{path}{query}{body}"
        mac = hmac.new(self.api_secret, message.encode(), hashlib.sha256).digest()
        return base64.b64encode(mac).decode()

    # ------------------------------------------------------------
    # HTTP REQUEST (signed or public)
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

        if params is None:
            params = {}

        # Build query string (must be sorted!)
        query = ""
        if params:
            keyvals = "&".join(f"{k}={v}" for k, v in sorted(params.items()))
            query = f"?{keyvals}"

        url = self.BASE + path + query

        body = ""
        if data:
            import json
            body = json.dumps(data, separators=(",", ":"))

        headers = {}
        timestamp = str(int(time.time() * 1000))

        if auth:
            sign = self._sign(timestamp, method.upper(), path, query, body)
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
                data=body if data else None,
                headers=headers,
                timeout=20,
            ) as resp:

                txt = await resp.text()
                import json

                # Attempt parse
                try:
                    js = json.loads(txt)
                except:
                    return {"code": resp.status, "msg": txt}

                # Uniformisation Bitget
                if "code" in js and js["code"] != "00000":
                    return js

                return js

        except Exception as e:
            return {"code": -1, "msg": str(e)}

    # ------------------------------------------------------------
    # PUBLIC MARKET DATA
    # ------------------------------------------------------------
    async def get_klines(
        self,
        symbol: str,
        granularity: str = "1h",
        limit: int = 200,
    ):
        """
        Retourne OHLCV format :
        [
          [timestamp, open, high, low, close, volume]
        ]
        """

        # Bitget granularity uses strings: "1m","5m","1h","4h","1d"
        tf_map = {
            "1m": "1m",
            "5m": "5m",
            "15m": "15m",
            "1h": "1H",
            "4h": "4H",
            "1d": "1D",
        }

        gran = tf_map.get(granularity.lower(), "1H")

        r = await self._request(
            "GET",
            "/api/mix/v1/market/candles",
            params={
                "symbol": symbol,
                "granularity": gran,
                "limit": limit
            },
            auth=False,
        )

        return r.get("data", [])

    # ------------------------------------------------------------
    async def get_contract(self, symbol: str):
        """Infos du contrat Bitget (lotSize, tickSize, multiplier...)"""
        r = await self._request("GET", "/api/mix/v1/market/contracts", auth=False)
        for c in r.get("data", []):
            if c.get("symbol") == symbol:
                return c
        return None

    # ------------------------------------------------------------
    # POSITIONS
    # ------------------------------------------------------------
    async def get_position(self, symbol: str):
        r = await self._request(
            "GET",
            "/api/mix/v1/position/singlePosition",
            params={"symbol": symbol, "marginCoin": "USDT"},
        )
        return r.get("data", {})

    # ------------------------------------------------------------
    # INSTITUTIONAL METRICS
    # ------------------------------------------------------------
    async def get_open_interest(self, symbol: str) -> Optional[float]:
        r = await self._request(
            "GET",
            "/api/mix/v1/market/openInterest",
            params={"symbol": symbol},
            auth=False
        )
        try:
            return float(r.get("data", {}).get("openInterest", 0))
        except:
            return None

    async def get_funding_rate(self, symbol: str) -> Optional[float]:
        r = await self._request(
            "GET",
            "/api/mix/v1/market/fundingRate",
            params={"symbol": symbol},
            auth=False
        )
        try:
            return float(r.get("data", {}).get("rate", 0))
        except:
            return None

    # ------------------------------------------------------------
    # CLOSE SESSION
    # ------------------------------------------------------------
    async def close(self):
        if self.session and not self.session.closed:
            await self.session.close()


# =====================================================================
# Client unique (cache)
# =====================================================================
_client_cache: Optional[BitgetClient] = None

async def get_client(api_key: str, api_secret: str, api_passphrase: str) -> BitgetClient:
    global _client_cache
    if _client_cache is None:
        _client_cache = BitgetClient(api_key, api_secret, api_passphrase)
    return _client_cache
