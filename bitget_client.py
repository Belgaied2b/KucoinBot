# =====================================================================
# bitget_client.py — Desk Lead Edition 2025
# =====================================================================
# Client REST Bitget Futures USDT-M ultra robuste :
#   ✔ API v2 compatible (no deprecated endpoints)
#   ✔ RATE LIMITER global anti-429
#   ✔ Auto-retry exponentiel intelligent
#   ✔ get_klines_df() → DataFrame OHLCV propre
#   ✔ Contract / OI / Funding caching
#   ✔ Async-safe — compatible Scanner/Analyzer/Trader
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
from typing import Any, Dict, Optional

LOGGER = logging.getLogger(__name__)

# =====================================================================
# GLOBAL RATE LIMITER (Anti 429)
# =====================================================================

_RATE_LIMIT_LOCK = asyncio.Lock()
_LAST_REQUEST_TS = 0
MIN_REQUEST_DELAY = 0.25   # 4 req/sec (safe for Bitget futures)


# =====================================================================
# RETRY ENGINE
# =====================================================================

async def _async_backoff_retry(fn, retries=4, base_delay=0.35, exc=(Exception,)):
    for attempt in range(retries + 1):
        try:
            return await fn()
        except exc as e:
            if attempt >= retries:
                raise
            await asyncio.sleep(base_delay * (2 ** attempt))


# =====================================================================
# KUCOIN → BITGET SYMBOL MAP
# =====================================================================

def map_symbol_kucoin_to_bitget(sym: str) -> Optional[str]:
    """
    Examples:
        BTCUSDTM  → BTCUSDT
        ETHUSDTM  → ETHUSDT
        XBTUSDTM  → BTCUSDT
    """
    if not sym:
        return None

    s = sym.upper().replace("USDTM", "").replace("-USDTM", "").replace("USDM", "")
    if s == "XBT":
        s = "BTC"

    return f"{s}USDT"       # NEW Bitget v2 format uses NO "_UMCBL" suffix.


# =====================================================================
# BitgetClient (v2 API compliant)
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

    # ------------------------------------------------------------------
    async def _ensure_session(self):
        if self.session is None or self.session.closed:
            timeout = aiohttp.ClientTimeout(total=25)
            self.session = aiohttp.ClientSession(timeout=timeout)

    # ------------------------------------------------------------------
    # SIGNATURE (unchanged)
    # ------------------------------------------------------------------
    def _sign(self, ts: str, method: str, path: str, query: str = "", body: str = "") -> str:
        msg = f"{ts}{method}{path}{query}{body}"
        mac = hmac.new(self.api_secret, msg.encode(), hashlib.sha256).digest()
        return base64.b64encode(mac).decode()

    # ------------------------------------------------------------------
    # HTTP REQUEST v2 + RATE LIMITER
    # ------------------------------------------------------------------
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

        # Build query string
        query = ""
        if params:
            qs = "&".join(f"{k}={v}" for k, v in sorted(params.items()))
            query = f"?{qs}"

        url = self.BASE + path + query
        body = json.dumps(data, separators=(",", ":")) if data else ""

        async def _do():
            global _LAST_REQUEST_TS

            # RATE LIMIT SECTION
            async with _RATE_LIMIT_LOCK:
                now = time.time()
                elapsed = now - _LAST_REQUEST_TS
                if elapsed < MIN_REQUEST_DELAY:
                    await asyncio.sleep(MIN_REQUEST_DELAY - elapsed)
                _LAST_REQUEST_TS = time.time()

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

            async with self.session.request(
                method.upper(), url, headers=headers, data=body if data else None
            ) as resp:

                txt = await resp.text()

                # Retry on 429 / 5xx
                if resp.status == 429:
                    raise ConnectionError("429 Too Many Requests")

                if 500 <= resp.status < 600:
                    raise ConnectionError(f"Retryable server error {resp.status}")

                # Try parse JSON
                try:
                    js = json.loads(txt)
                except:
                    return {"ok": False, "status": resp.status, "raw": txt}

                ok = js.get("code") == "00000"
                return {
                    "ok": ok,
                    "status": resp.status,
                    "data": js.get("data"),
                    "raw": js,
                }

        return await _async_backoff_retry(_do, retries=retries)


    # =====================================================================
    # MARKET DATA v2
    # =====================================================================

    async def get_klines_df(self, symbol: str, tf: str = "1H", limit: int = 200) -> pd.DataFrame:

        mapped = map_symbol_kucoin_to_bitget(symbol)
        if not mapped:
            return pd.DataFrame()

        # NEW v2 endpoint:
        r = await self._request(
            "GET",
            "/api/v2/market/history-candles",
            params={"symbol": mapped, "granularity": tf, "limit": limit},
            auth=False,
        )

        raw = r.get("data") or []
        if not raw:
            return pd.DataFrame()

        try:
            df = pd.DataFrame(raw, columns=[
                "timestamp", "open", "high", "low", "close", "volume"
            ])

            for c in df.columns:
                df[c] = df[c].astype(float)

            return df.sort_values("timestamp").reset_index(drop=True)

        except Exception:
            LOGGER.exception(f"Failed to parse klines for {symbol}")
            return pd.DataFrame()


    # =====================================================================
    # CONTRACT METADATA v2
    # =====================================================================

    async def get_contract(self, symbol: str) -> Dict[str, Any]:
        now = time.time()

        if symbol in self._contract_cache and now - self._contract_ts < 300:
            return self._contract_cache[symbol]

        r = await self._request("GET", "/api/v2/market/contracts", auth=False)

        data = r.get("data") or []
        mapped = map_symbol_kucoin_to_bitget(symbol)

        for c in data:
            if c.get("symbol") == mapped:
                self._contract_cache[symbol] = c
                self._contract_ts = now
                return c

        return {}


    # =====================================================================
    # POSITION v2
    # =====================================================================

    async def get_position(self, symbol: str) -> Dict[str, Any]:
        mapped = map_symbol_kucoin_to_bitget(symbol)
        if not mapped:
            return {}

        r = await self._request(
            "GET",
            "/api/v2/mix/position/single-position",
            params={"symbol": mapped, "marginCoin": "USDT"},
        )

        return r.get("data") or {}


    # =====================================================================
    # OPEN INTEREST v2
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
            "/api/v2/market/open-interest",
            params={"symbol": mapped},
            auth=False,
        )

        try:
            oi = float(r.get("data", {}).get("openInterest", 0))
        except:
            oi = None

        self._cache_oi[symbol] = {"ts": now, "val": oi}
        return oi

    # =====================================================================
    # FUNDING RATE v2
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
            "/api/v2/market/funding-rate",
            params={"symbol": mapped},
            auth=False,
        )

        try:
            rate = float(r.get("data", {}).get("rate", 0))
        except:
            rate = None

        self._cache_funding[symbol] = {"ts": now, "val": rate}
        return rate


    # =====================================================================
    # MARK PRICE v2
    # =====================================================================

    async def get_mark_price(self, symbol: str) -> Optional[float]:
        mapped = map_symbol_kucoin_to_bitget(symbol)
        if not mapped:
            return None

        r = await self._request(
            "GET",
            "/api/v2/market/mark-price",
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
# Singleton
# =====================================================================

_client_cache: Optional[BitgetClient] = None

async def get_client(api_key: str, api_secret: str, api_passphrase: str) -> BitgetClient:
    global _client_cache
    if _client_cache is None:
        _client_cache = BitgetClient(api_key, api_secret, api_passphrase)
    return _client_cache
