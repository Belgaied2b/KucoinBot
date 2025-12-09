# =====================================================================
# bitget_client.py — Bitget API v2 (2025) FINAL FIXED
# FULL support:
#   • Symbol normalization
#   • /contracts v2
#   • /candles v2 with correct granularity mapping
#   • Retry engine
#   • DataFrame parser
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

async def _async_backoff_retry(fn, *, retries=4, base_delay=0.3):
    for attempt in range(retries + 1):
        try:
            return await fn()
        except Exception:
            if attempt >= retries:
                raise
            await asyncio.sleep(base_delay * (2 ** attempt))


# =====================================================================
# SYMBOL NORMALISATION
# =====================================================================

def normalize_symbol(sym: str) -> str:
    if not sym:
        return ""

    s = sym.upper().replace("-", "")
    s = s.replace("USDTM", "USDT").replace("USDTSWAP", "USDT")

    if s.startswith("XBT"):
        s = s.replace("XBT", "BTC")

    return s


def format_symbol(sym: str) -> str:
    return normalize_symbol(sym)


# =====================================================================
# TF MAPPING — CRITICAL FIX
# =====================================================================

TF_MAP = {
    "1M": 60,
    "5M": 300,
    "15M": 900,
    "30M": 1800,
    "1H": 3600,
    "4H": 14400,
    "1D": 86400,
}


# =====================================================================
# CLIENT REST
# =====================================================================

class BitgetClient:
    BASE = "https://api.bitget.com"

    def __init__(self, api_key: str, api_secret: str, api_passphrase: str):
        self.api_key = api_key
        self.api_secret = api_secret.encode()
        self.api_passphrase = api_passphrase

        self.session: Optional[aiohttp.ClientSession] = None

        # Cache symboles
        self._contracts_cache = None
        self._contracts_ts = 0

    # ---------------------------------------------------------------
    async def _ensure_session(self):
        if self.session is None or self.session.closed:
            timeout = aiohttp.ClientTimeout(total=25)
            self.session = aiohttp.ClientSession(timeout=timeout)

    # ---------------------------------------------------------------
    def _sign(self, ts: str, method: str, path: str, query: str, body: str):
        msg = f"{ts}{method}{path}{query}{body}"
        mac = hmac.new(self.api_secret, msg.encode(), hashlib.sha256).digest()
        return base64.b64encode(mac).decode()

    # ---------------------------------------------------------------
    async def _request(self, method: str, path: str, *, params=None, data=None, auth=True):
        await self._ensure_session()

        params = params or {}
        data = data or {}

        query = ""
        if params:
            query = "?" + "&".join(f"{k}={v}" for k, v in params.items())

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
                    "Content-Type": "application/json"
                }

            async with self.session.request(
                method.upper(), url, headers=headers, data=body if data else None
            ) as resp:

                txt = await resp.text()

                try:
                    js = json.loads(txt)
                except:
                    LOGGER.error(f"❌ JSON parse error: {txt}")
                    return {"code": "99999", "msg": "json error", "raw": txt}

                return js

        return await _async_backoff_retry(_do)

    # =====================================================================
    # CONTRACT LIST
    # =====================================================================

    async def get_contracts_list(self) -> List[str]:
        now = time.time()

        if self._contracts_cache and now - self._contracts_ts < 300:
            return self._contracts_cache

        r = await self._request(
            "GET",
            "/api/v2/mix/market/contracts",
            params={"productType": "USDT-FUTURES"},
            auth=False,
        )

        if not isinstance(r, dict) or "data" not in r:
            LOGGER.error(f"📡 CONTRACT LIST ERROR: {r}")
            return []

        symbols = [format_symbol(c["symbol"]) for c in r["data"] if "symbol" in c]

        LOGGER.info(f"📈 Loaded {len(symbols)} symbols from Bitget v2")

        self._contracts_cache = symbols
        self._contracts_ts = now
        return symbols

    # =====================================================================
    # KLINES (v2)
    # =====================================================================

    async def get_klines_df(self, symbol: str, tf: str = "1H", limit: int = 200):
        sym = format_symbol(symbol)

        gran = TF_MAP.get(tf.upper(), 3600)  # Default 1H

        r = await self._request(
            "GET",
            "/api/v2/mix/market/candles",
            params={"symbol": sym, "granularity": gran, "limit": limit},
            auth=False,
        )

        data = r.get("data", [])

        if not data:
            LOGGER.warning(f"⚠️ Empty klines for {sym} ({tf})")
            return pd.DataFrame()

        try:
            df = pd.DataFrame(
                data,
                columns=["time", "open", "high", "low", "close", "volume"]
            )

            df = df.astype(float)
            df.sort_values("time", inplace=True)

            return df.reset_index(drop=True)

        except Exception as e:
            LOGGER.exception(f"❌ Error parsing klines for {symbol}: {e}")
            return pd.DataFrame()


# =====================================================================
# SINGLETON
# =====================================================================

_client_instance: Optional[BitgetClient] = None

async def get_client(api_key: str, api_secret: str, api_passphrase: str):
    global _client_instance
    if _client_instance is None:
        _client_instance = BitgetClient(api_key, api_secret, api_passphrase)
    return _client_instance
