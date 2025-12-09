# =====================================================================
# bitget_client.py — Desk Lead Edition (2025, API V2 COMPLIANT)
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
# BACKOFF RETRY
# =====================================================================

async def _async_backoff_retry(fn, retries=4, base_delay=0.35):
    for attempt in range(retries + 1):
        try:
            return await fn()
        except Exception as e:
            if attempt >= retries:
                raise
            await asyncio.sleep(base_delay * (2 ** attempt))


# =====================================================================
# KUCOIN → BITGET SYMBOL
# =====================================================================

def map_symbol_kucoin_to_bitget(sym: str) -> Optional[str]:
    """
    KuCoin: BTCUSDTM → Bitget: BTCUSDT_UMCBL
    """
    if not sym:
        return None
    s = sym.upper().replace("USDTM", "").replace("USDM", "").replace("-USDTM", "")
    if s == "XBT":
        s = "BTC"
    return f"{s}USDT_UMCBL"


# =====================================================================
# API CLIENT V2
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

    # -------------------------------------------------------------
    async def _ensure_session(self):
        if self.session is None or self.session.closed:
            timeout = aiohttp.ClientTimeout(total=25)
            self.session = aiohttp.ClientSession(timeout=timeout)

    # -------------------------------------------------------------
    def _sign(self, ts: str, method: str, path: str, body: str = "") -> str:
        msg = f"{ts}{method}{path}{body}"
        mac = hmac.new(self.api_secret, msg.encode(), hashlib.sha256).digest()
        return base64.b64encode(mac).decode()

    # -------------------------------------------------------------
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

        # Build URL
        query = ""
        if params:
            query = "?" + "&".join(f"{k}={v}" for k, v in sorted(params.items()))

        url = self.BASE + path + query
        body = json.dumps(data, separators=(",", ":")) if data else ""

        async def _do():
            ts = str(int(time.time() * 1000))
            headers = {}

            if auth:
                sig = self._sign(ts, method.upper(), path, body)
                headers = {
                    "ACCESS-KEY": self.api_key,
                    "ACCESS-SIGN": sig,
                    "ACCESS-TIMESTAMP": ts,
                    "ACCESS-PASSPHRASE": self.api_passphrase,
                    "Content-Type": "application/json",
                }

            async with self.session.request(
                method.upper(),
                url,
                headers=headers,
                data=body if data else None
            ) as resp:

                txt = await resp.text()

                # Retry on 429 or 5xx
                if resp.status == 429 or resp.status >= 500:
                    raise ConnectionError(f"Retryable status {resp.status}: {txt}")

                try:
                    js = json.loads(txt)
                except:
                    return {"ok": False, "raw": txt}

                ok = js.get("code") == "00000"
                return {"ok": ok, "status": resp.status, "data": js.get("data"), "raw": js}

        return await _async_backoff_retry(_do, retries=retries)

    # ==================================================================
    # MARKET DATA (V2)
    # ==================================================================

    async def get_klines_df(self, symbol: str, tf: str = "1H", limit: int = 200) -> pd.DataFrame:
        mapped = map_symbol_kucoin_to_bitget(symbol)
        if not mapped:
            return pd.DataFrame()

        r = await self._request(
            "GET",
            "/api/v2/mix/market/candles",
            params={"symbol": mapped, "granularity": tf, "limit": limit},
            auth=False,
        )

        raw = r.get("data")
        if not raw:
            return pd.DataFrame()

        try:
            df = pd.DataFrame(raw, columns=["time", "open", "high", "low", "close", "volume"])
            for c in df.columns:
                df[c] = df[c].astype(float)
            return df.sort_values("time").reset_index(drop=True)
        except:
            LOGGER.exception("Failed parsing klines")
            return pd.DataFrame()

    # ==================================================================
    # CONTRACTS (V2)
    # ==================================================================

    async def get_contracts(self):
        r = await self._request(
            "GET",
            "/api/v2/mix/market/contracts",
            params={"productType": "umcbl"},
            auth=False,
        )
        return r

    async def get_contract(self, symbol: str) -> Dict[str, Any]:
        now = time.time()

        if symbol in self._contract_cache and now - self._contract_ts < 300:
            return self._contract_cache[symbol]

        r = await self.get_contracts()

        for c in r.get("data") or []:
            if c.get("symbol") == map_symbol_kucoin_to_bitget(symbol):
                self._contract_cache[symbol] = c
                self._contract_ts = now
                return c

        return {}

    # ==================================================================
    # OPEN INTEREST (V2)
    # ==================================================================

    async def get_open_interest(self, symbol: str) -> Optional[float]:
        mapped = map_symbol_kucoin_to_bitget(symbol)

        r = await self._request(
            "GET",
            "/api/v2/mix/market/open-interest",
            params={"symbol": mapped},
            auth=False,
        )

        try:
            return float(r["data"]["openInterest"])
        except:
            return None

    # ==================================================================
    # FUNDING RATE (V2)
    # ==================================================================

    async def get_funding_rate(self, symbol: str) -> Optional[float]:

        mapped = map_symbol_kucoin_to_bitget(symbol)

        r = await self._request(
            "GET",
            "/api/v2/mix/market/funding-rate",
            params={"symbol": mapped},
            auth=False,
        )

        try:
            return float(r["data"]["rate"])
        except:
            return None

    # ==================================================================
    # MARK PRICE (V2)
    # ==================================================================

    async def get_mark_price(self, symbol: str) -> Optional[float]:
        mapped = map_symbol_kucoin_to_bitget(symbol)

        r = await self._request(
            "GET",
            "/api/v2/mix/market/mark-price",
            params={"symbol": mapped},
            auth=False,
        )

        try:
            return float(r["data"]["markPrice"])
        except:
            return None


# =====================================================================
# SINGLETON CLIENT
# =====================================================================

_client_cache: Optional[BitgetClient] = None

async def get_client(api_key: str, api_secret: str, api_passphrase: str) -> BitgetClient:
    global _client_cache
    if _client_cache is None:
        _client_cache = BitgetClient(api_key, api_secret, api_passphrase)
    return _client_cache
