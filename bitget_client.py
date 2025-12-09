# =====================================================================
# bitget_client.py — Desk Lead Edition (2025)
# =====================================================================
# Ultra-robust Bitget REST Client:
#   ✔ Auto-retry exponential
#   ✔ KuCoin → Bitget symbol mapping
#   ✔ Contract / OI / Funding caching
#   ✔ get_klines_df() → clean OHLCV DataFrame
#   ✔ get_klines()  → KuCoin-style raw OHLCV
#   ✔ Proper Bitget signature
#   ✔ Async-safe, scanner-compatible
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
    """
    KuCoin: BTCUSDTM, ETHUSDTM, XBTUSDTM
    Bitget: BTCUSDT_UMCBL, ETHUSDT_UMCBL
    """
    if not sym:
        return None

    s = sym.upper().replace("USDTM", "").replace("USDM", "").replace("-USDTM", "")

    if s == "XBT":
        s = "BTC"

    return f"{s}USDT_UMCBL"


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

        # Caches
        self._contract_cache = {}
        self._contract_ts = 0
        self._cache_oi = {}
        self._cache_funding = {}

    # -----------------------------------------------------------------
    async def _ensure_session(self):
        if self.session is None or self.session.closed:
            timeout = aiohttp.ClientTimeout(total=25)
            self.session = aiohttp.ClientSession(timeout=timeout)

    # -----------------------------------------------------------------
    def _sign(self, ts: str, method: str, path: str, query: str = "", body: str = "") -> str:
        msg = f"{ts}{method}{path}{query}{body}"
        mac = hmac.new(self.api_secret, msg.encode(), hashlib.sha256).digest()
        return base64.b64encode(mac).decode()

    # -----------------------------------------------------------------
    async def _request(
        self,
        method: str,
        path: str,
        *,
        params: Dict[str, Any] = None,
        data: Dict[str, Any] = None,
        auth: bool = True,
        retries: int = 4,
    ) -> Dict[str, Any]:

        await self._ensure_session()

        params = params or {}
        data = data or {}

        # Build query
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
                signature = self._sign(ts, method.upper(), path, query, body)
                headers = {
                    "ACCESS-KEY": self.api_key,
                    "ACCESS-SIGN": signature,
                    "ACCESS-TIMESTAMP": ts,
                    "ACCESS-PASSPHRASE": self.api_passphrase,
                    "Content-Type": "application/json",
                }

            async with self.session.request(method.upper(), url, headers=headers, data=body if data else None) as resp:
                txt = await resp.text()

                # Retryable responses
                if resp.status == 429 or 500 <= resp.status < 600:
                    raise ConnectionError(f"Retryable {resp.status}: {txt}")

                try:
                    js = json.loads(txt)
                except:
                    return {"ok": False, "status": resp.status, "raw": txt}

                return {
                    "ok": js.get("code") == "00000",
                    "status": resp.status,
                    "data": js.get("data"),
                    "raw": js,
                }

        return await _async_backoff_retry(_do, retries=retries)

    # =====================================================================
    # MARKET DATA
    # =====================================================================

    async def get_klines_df(self, symbol: str, tf: str = "1H", limit: int = 200) -> pd.DataFrame:
        """Return OHLCV as a clean DataFrame."""
        mapped = map_symbol_kucoin_to_bitget(symbol)
        if not mapped:
            return pd.DataFrame()

        r = await self._request(
            "GET",
            "/api/mix/v1/market/candles",
            params={"symbol": mapped, "granularity": tf, "limit": limit},
            auth=False,
        )

        raw = r.get("data")
        if not raw:
            return pd.DataFrame()

        try:
            df = pd.DataFrame(raw, columns=["time", "open", "high", "low", "close", "volume"])
            df = df.astype(float)
            return df.sort_values("time").reset_index(drop=True)
        except Exception:
            LOGGER.exception("Failed klines parse for %s", symbol)
            return pd.DataFrame()

    # =====================================================================
    # LEGACY COMPATIBILITY — get_klines()
    # =====================================================================

    async def get_klines(self, symbol: str, tf: str = "1H", limit: int = 200):
        """
        Legacy wrapper → returns KuCoin-style OHLCV list:
        [
            [timestamp, open, high, low, close, volume],
            ...
        ]
        Required by scanner.py + analyze_signal
        """
        df = await self.get_klines_df(symbol, tf, limit)

        if df.empty:
            return []

        out = []
        for _, row in df.iterrows():
            out.append([
                row["time"],
                row["open"],
                row["high"],
                row["low"],
                row["close"],
                row["volume"],
            ])
        return out

    # =====================================================================
    # CONTRACT METADATA
    # =====================================================================

    async def get_contract(self, symbol: str) -> Dict[str, Any]:
        now = time.time()

        if symbol in self._contract_cache and now - self._contract_ts < 300:
            return self._contract_cache[symbol]

        mapped = map_symbol_kucoin_to_bitget(symbol)
        if not mapped:
            return {}

        r = await self._request("GET", "/api/mix/v1/market/contracts", auth=False)

        for c in r.get("data") or []:
            if c.get("symbol") == mapped:
                self._contract_cache[symbol] = c
                self._contract_ts = now
                return c

        return {}

    # =====================================================================
    # POSITION
    # =====================================================================

    async def get_position(self, symbol: str) -> Dict[str, Any]:
        mapped = map_symbol_kucoin_to_bitget(symbol)
        if not mapped:
            return {}

        r = await self._request(
            "GET",
            "/api/mix/v1/position/singlePosition",
            params={"symbol": mapped, "marginCoin": "USDT"},
        )
        return r.get("data") or {}

    # =====================================================================
    # OPEN INTEREST
    # =====================================================================

    async def get_open_interest(self, symbol: str) -> Optional[float]:
        mapped = map_symbol_kucoin_to_bitget(symbol)
        if not mapped:
            return None

        now = time.time()

        if symbol in self._cache_oi and now - self._cache_oi[symbol]["ts"] < 3:
            return self._cache_oi[symbol]["val"]

        r = await self._request(
            "GET",
            "/api/mix/v1/market/openInterest",
            params={"symbol": mapped},
            auth=False,
        )

        try:
            val = float(r.get("data", {}).get("openInterest", 0))
        except:
            val = None

        self._cache_oi[symbol] = {"ts": now, "val": val}
        return val

    # =====================================================================
    # FUNDING RATE
    # =====================================================================

    async def get_funding_rate(self, symbol: str) -> Optional[float]:
        mapped = map_symbol_kucoin_to_bitget(symbol)
        if not mapped:
            return None

        now = time.time()

        if symbol in self._cache_funding and now - self._cache_funding[symbol]["ts"] < 3:
            return self._cache_funding[symbol]["val"]

        r = await self._request(
            "GET",
            "/api/mix/v1/market/fundingRate",
            params={"symbol": mapped},
            auth=False,
        )

        try:
            val = float(r.get("data", {}).get("rate", 0))
        except:
            val = None

        self._cache_funding[symbol] = {"ts": now, "val": val}
        return val

    # =====================================================================
    # MARK PRICE
    # =====================================================================

    async def get_mark_price(self, symbol: str) -> Optional[float]:
        mapped = map_symbol_kucoin_to_bitget(symbol)
        if not mapped:
            return None

        r = await self._request(
            "GET",
            "/api/mix/v1/market/mark-price",
            params={"symbol": mapped},
            auth=False,
        )

        try:
            return float(r.get("data", {}).get("markPrice"))
        except:
            return None

    # =====================================================================
    async def close(self):
        if self.session and not self.session.closed:
            await self.session.close()


# =====================================================================
# SINGLETON
# =====================================================================

_client_cache: Optional[BitgetClient] = None

async def get_client(api_key: str, api_secret: str, api_passphrase: str) -> BitgetClient:
    global _client_cache

    if _client_cache is None:
        _client_cache = BitgetClient(api_key, api_secret, api_passphrase)

    return _client_cache
