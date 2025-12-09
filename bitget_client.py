# =====================================================================
# bitget_client.py — Bitget API v2 (2025) FULLY FIXED
# Compatible scanner.py / analyze_signal.py / trader v2
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
# SYMBOL NORMALIZATION — v2 (NO UMCBL ANYMORE)
# =====================================================================

def normalize_symbol(sym: str) -> str:
    """
    Convertit tout symbole en format Bitget v2
    EX :
        BTCUSDTM → BTCUSDT
        BTC-USDT → BTCUSDT
        BTCUSDT  → BTCUSDT
        XBTUSDT  → BTCUSDT
    """
    if not sym:
        return ""

    s = sym.replace("-", "").replace("USDTM", "USDT").upper()

    if s.startswith("XBT"):
        s = s.replace("XBT", "BTC")

    return s


def add_suffix(sym: str) -> str:
    """
    Depuis 2024 → Bitget utilise *UNIQUEMENT* BTCUSDT
    AUCUN suffixe _UMCBL ou _UMCBL
    """
    return sym  # FINAL : aucun suffixe


# =====================================================================
# CLIENT
# =====================================================================

class BitgetClient:
    BASE = "https://api.bitget.com"

    def __init__(self, api_key: str, api_secret: str, api_passphrase: str):
        self.api_key = api_key
        self.api_secret = api_secret.encode()
        self.api_passphrase = api_passphrase
        self.session: Optional[aiohttp.ClientSession] = None

        self._contracts_cache = None
        self._contracts_ts = 0

    # ---------------------------------------------------------------
    async def _ensure_session(self):
        if self.session is None or self.session.closed:
            self.session = aiohttp.ClientSession()

    # ---------------------------------------------------------------
    def _sign(self, ts: str, method: str, path: str, query: str, body: str):
        msg = f"{ts}{method}{path}{query}{body}"
        mac = hmac.new(self.api_secret, msg.encode(), hashlib.sha256).digest()
        return base64.b64encode(mac).decode()

    # ---------------------------------------------------------------
    async def _request(
        self,
        method: str,
        path: str,
        *,
        params=None,
        data=None,
        auth=True,
    ):
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
                method.upper(),
                url,
                headers=headers,
                data=body if data else None,
            ) as resp:

                txt = await resp.text()
                try:
                    js = json.loads(txt)
                except:
                    return {"ok": False, "status": resp.status, "raw": txt}

                return js

        return await _async_backoff_retry(_do)

    # =====================================================================
    # CONTRACT LIST : API v2
    # =====================================================================
    async def get_contracts_list(self) -> List[str]:
        """
        Nouveau endpoint v2 :
        /api/v2/mix/market/contracts?productType=USDT-FUTURES
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

        if not isinstance(r, dict) or "data" not in r:
            LOGGER.error(f"📡 CONTRACT LIST ERROR: {r}")
            return []

        symbols = [c["symbol"] for c in r["data"] if "symbol" in c]

        self._contracts_cache = symbols
        self._contracts_ts = now

        LOGGER.info(f"📈 Loaded {len(symbols)} symbols from Bitget v2")

        return symbols

    # =====================================================================
    # KLINES
    # =====================================================================
    async def get_klines_df(self, symbol: str, tf: str = "1H", limit: int = 200):
        sym = add_suffix(normalize_symbol(symbol))

        r = await self._request(
            "GET",
            "/api/v2/mix/market/candles",
            params={"symbol": sym, "granularity": tf, "limit": limit},
            auth=False,
        )

        if "data" not in r or not r["data"]:
            return pd.DataFrame()

        try:
            df = pd.DataFrame(
                r["data"],
                columns=["ts", "open", "high", "low", "close", "volume"]
            )
            df = df.astype(float)
            df.rename(columns={"ts": "time"}, inplace=True)
            df.sort_values("time", inplace=True)
            return df.reset_index(drop=True)
        except Exception:
            LOGGER.exception(f"Error parsing klines for {symbol}")
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
