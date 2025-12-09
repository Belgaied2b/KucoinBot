# =====================================================================
# bitget_client.py — Desk Lead Edition (2025)
# =====================================================================
# Client REST Bitget Futures (USDT-M) v2
#   ✔ get_contracts_list() — récupère tous les contrats PERP
#   ✔ get_klines_df() — OHLCV propre
#   ✔ Contract cache / OI / Funding
#   ✔ API v2 officielle
# =====================================================================

from __future__ import annotations

import aiohttp
import asyncio
import time
import hmac
import base64
import hashlib
import json
import logging
import pandas as pd
from typing import Any, Dict, Optional, List

LOGGER = logging.getLogger(__name__)

# =====================================================================
# RETRY ENGINE
# =====================================================================

async def _async_backoff_retry(fn, retries=4, base_delay=0.35, exc=(Exception,)):
    for attempt in range(retries + 1):
        try:
            return await fn()
        except exc:
            if attempt >= retries:
                raise
            await asyncio.sleep(base_delay * (2 ** attempt))

# =====================================================================
# KUCOIN → BITGET SYMBOL MAPPING
# =====================================================================

def map_symbol_kucoin_to_bitget(sym: str) -> Optional[str]:
    if not sym:
        return None
    s = sym.upper().replace("USDTM", "").replace("USDM", "").replace("-USDTM", "")
    if s == "XBT":
        s = "BTC"
    return f"{s}USDT"

# =====================================================================
# BitgetClient v2
# =====================================================================

class BitgetClient:
    BASE = "https://api.bitget.com"

    def __init__(self, api_key: str, api_secret: str, api_passphrase: str):
        self.api_key = api_key
        self.api_secret = api_secret.encode()
        self.api_passphrase = api_passphrase

        self.session: Optional[aiohttp.ClientSession] = None

        # Caches
        self._contracts_cache: Optional[List[dict]] = None
        self._contracts_ts = 0

    # ------------------------------------------------------------
    async def _ensure_session(self):
        if self.session is None or self.session.closed:
            timeout = aiohttp.ClientTimeout(total=20)
            self.session = aiohttp.ClientSession(timeout=timeout)

    # ------------------------------------------------------------
    def _sign(self, ts: str, method: str, path: str, query: str, body: str) -> str:
        msg = f"{ts}{method}{path}{query}{body}"
        mac = hmac.new(self.api_secret, msg.encode(), hashlib.sha256).digest()
        return base64.b64encode(mac).decode()

    # ------------------------------------------------------------
    async def _request(self, method: str, path: str, *, params=None, data=None, auth=True):
        await self._ensure_session()

        params = params or {}
        data = data or {}

        query = ""
        if params:
            qs = "&".join(f"{k}={v}" for k, v in sorted(params.items()))
            query = f"?{qs}"

        url = self.BASE + path + query
        body = json.dumps(data, separators=(",", ":")) if data else ""

        async def _do():
            ts = str(int(time.time() * 1000))
            headers = {}

            if auth:
                sig = self._sign(ts, method.upper(), path, query, body)
                headers = {
                    "ACCESS-KEY": self.api_key,
                    "ACCESS-SIGN": sig,
                    "ACCESS-TIMESTAMP": ts,
                    "ACCESS-PASSPHRASE": self.api_passphrase,
                    "Content-Type": "application/json",
                }

            async with self.session.request(method.upper(), url, headers=headers, data=body) as resp:
                txt = await resp.text()

                # retry
                if resp.status in (429,) or 500 <= resp.status < 600:
                    raise ConnectionError(f"Retryable HTTP {resp.status}: {txt}")

                try:
                    js = json.loads(txt)
                except:
                    return {"ok": False, "status": resp.status, "raw": txt}

                ok = js.get("code") == "00000"
                return {
                    "ok": ok,
                    "status": resp.status,
                    "raw": js,
                    "data": js.get("data"),
                }

        return await _async_backoff_retry(_do)

    # =====================================================================
    # GET ALL CONTRACTS (PERPETUAL)
    # =====================================================================

    async def get_contracts_list(self) -> List[str]:
        """
        Retourne une liste de symboles Bitget perpétuels (BTCUSDT, ETHUSDT, etc.)
        """
        now = time.time()
        if self._contracts_cache and now - self._contracts_ts < 300:
            return self._contracts_cache

        r = await self._request(
            "GET",
            "/api/v2/mix/market/contracts",
            params={"productType": "USDT-FUTURES"},
            auth=False,
        )

        data = r.get("data") or []

        symbols = [c["symbol"] for c in data if c.get("symbolStatus") == "normal"]

        self._contracts_cache = symbols
        self._contracts_ts = now

        return symbols

    # =====================================================================
    # OHLCV
    # =====================================================================

    async def get_klines_df(self, symbol: str, tf: str = "1H", limit: int = 200) -> pd.DataFrame:
        r = await self._request(
            "GET",
            "/api/v2/mix/market/candles",
            params={"symbol": symbol, "granularity": tf, "limit": limit},
            auth=False,
        )

        raw = r.get("data") or []
        if not raw:
            return pd.DataFrame()

        try:
            df = pd.DataFrame(raw, columns=["time", "open", "high", "low", "close", "volume"])
            df = df.astype(float)
            return df.sort_values("time").reset_index(drop=True)
        except:
            return pd.DataFrame()

# =====================================================================
# SINGLETON
# =====================================================================

_client_cache: Optional[BitgetClient] = None

async def get_client(api_key: str, api_secret: str, api_passphrase: str) -> BitgetClient:
    global _client_cache
    if _client_cache is None:
        _client_cache = BitgetClient(api_key, api_secret, api_passphrase)
    return _client_cache
