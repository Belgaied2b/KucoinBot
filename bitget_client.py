# =====================================================================
# bitget_client.py — Bitget API v2 (2025) FINAL FIXED
# Full support for:
#   • /contracts v2
#   • /candles v2
#   • Symbol normalization (NO SUFFIX)
#   • Retry engine + stable parsing
# Compatible with:
#   scanner.py / analyze_signal.py / bitget_trader.py
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
        except Exception as e:
            if attempt >= retries:
                raise
            await asyncio.sleep(base_delay * (2 ** attempt))


# =====================================================================
# SYMBOL NORMALISATION (v2)
# =====================================================================

def normalize_symbol(sym: str) -> str:
    """
    Convertit tout format KuCoin, Bybit, ancien Bitget, etc.
    vers le format Bitget 2025 : BTCUSDT / ETHUSDT / SOLUSDT…
    """
    if not sym:
        return ""

    s = sym.upper().replace("-", "")

    # Remove margin suffixes KuCoin-style
    s = s.replace("USDTM", "USDT").replace("USDTSWAP", "USDT")

    # Convertir XBT → BTC
    if s.startswith("XBT"):
        s = s.replace("XBT", "BTC")

    return s


def format_symbol(sym: str) -> str:
    """
    Dans la version 2025 → Bitget Futures ne nécessite plus aucun suffixe.
    BTCUSDT est le format final.
    """
    return normalize_symbol(sym)


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

        # Build query string
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
    # CONTRACT LIST (v2)
    # =====================================================================

    async def get_contracts_list(self) -> List[str]:
        """
        Renvoie la liste des symboles Bitget Futures USDT (sans suffixe).
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

        symbols = []
        for c in r["data"]:
            sym = c.get("symbol")
            if not sym:
                continue
            symbols.append(format_symbol(sym))

        LOGGER.info(f"📈 Loaded {len(symbols)} symbols from Bitget v2")

        self._contracts_cache = symbols
        self._contracts_ts = now

        return symbols

    # =====================================================================
    # KLINES (v2)
    # =====================================================================

    async def get_klines_df(self, symbol: str, tf: str = "1H", limit: int = 200):
        sym = format_symbol(symbol)

        r = await self._request(
            "GET",
            "/api/v2/mix/market/candles",
            params={"symbol": sym, "granularity": tf, "limit": limit},
            auth=False,
        )

        if "data" not in r or not r["data"]:
            LOGGER.warning(f"⚠️ Empty klines for {sym} ({tf})")
            return pd.DataFrame()

        try:
            df = pd.DataFrame(
                r["data"],
                columns=["ts", "open", "high", "low", "close", "volume"]
            )

            df = df.astype(float)
            df.rename(columns={"ts": "time"}, inplace=True)

            # IMPORTANT : Bitget renvoie les chandeliers newest → oldest
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
