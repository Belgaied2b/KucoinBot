# =====================================================================
# bitget_client.py — DIAGNOSTIC VERSION (2025)
# OBJECTIF : identifier EXACTEMENT les symboles Bitget Futures
# et comprendre pourquoi l'API renvoie 400172 / 30032.
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

async def _async_retry(fn, retries=4, base_delay=0.3):
    for attempt in range(retries + 1):
        try:
            return await fn()
        except Exception as e:
            if attempt >= retries:
                raise
            await asyncio.sleep(base_delay * (2 ** attempt))


# =====================================================================
# CLIENT
# =====================================================================

class BitgetClient:
    BASE = "https://api.bitget.com"

    def __init__(self, api_key: str, api_secret: str, passphrase: str):
        self.api_key = api_key
        self.api_secret = api_secret.encode()
        self.api_passphrase = passphrase

        self.session: Optional[aiohttp.ClientSession] = None
        self._contracts_cache = None
        self._contracts_ts = 0

    # ---------------------------------------------------------------
    async def _ensure_session(self):
        if self.session is None or self.session.closed:
            timeout = aiohttp.ClientTimeout(total=25)
            self.session = aiohttp.ClientSession(timeout=timeout)

    # ---------------------------------------------------------------
    def _sign(self, ts, method, path, query, body):
        msg = f"{ts}{method}{path}{query}{body}"
        mac = hmac.new(self.api_secret, msg.encode(), hashlib.sha256).digest()
        return base64.b64encode(mac).decode()

    # ---------------------------------------------------------------
    async def _request(self, method, path, *, params=None, data=None, auth=True):
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
                sign = self._sign(ts, method.upper(), path, query, body)
                headers = {
                    "ACCESS-KEY": self.api_key,
                    "ACCESS-SIGN": sign,
                    "ACCESS-TIMESTAMP": ts,
                    "ACCESS-PASSPHRASE": self.api_passphrase,
                    "Content-Type": "application/json",
                }

            async with self.session.request(
                method.upper(), url, headers=headers, data=body or None
            ) as resp:

                raw_text = await resp.text()

                # LOG COMPLET
                LOGGER.error(f"🔍 RAW_RESPONSE {method} {path} params={params} → {raw_text}")

                # Try JSON
                try:
                    return json.loads(raw_text)
                except:
                    return {"error": "json_parse_error", "raw": raw_text}

        return await _async_retry(_do)

    # =====================================================================
    # CONTRACT LIST (API V2) — RAW
    # =====================================================================

    async def get_contracts_list(self) -> List[str]:
        now = time.time()

        if self._contracts_cache and now - self._contracts_ts < 300:
            return self._contracts_cache

        r = await self._request(
            "GET",
            "/api/v2/mix/market/contracts",
            params={"productType": "USDT-FUTURES"},
            auth=False
        )

        # RAW FULL LOG
        LOGGER.error(f"\n\n🔥🔥 RAW CONTRACTS RESPONSE (FULL) 🔥🔥\n{json.dumps(r, indent=4)}\n\n")

        if "data" not in r:
            LOGGER.error(f"❌ CONTRACT LIST ERROR → {r}")
            return []

        # Show first 40 symbols EXACTLY as returned
        first_40 = r["data"][:40]
        LOGGER.error(f"\n\n🔥 FIRST 40 CONTRACTS RAW 🔥\n{json.dumps(first_40, indent=4)}\n\n")

        # Just return the raw "symbol"
        symbols = [c["symbol"] for c in r["data"] if "symbol" in c]

        LOGGER.info(f"📌 Extracted {len(symbols)} raw symbols (no modification).")

        self._contracts_cache = symbols
        self._contracts_ts = now
        return symbols

    # =====================================================================
    # CANDLES (API V2 !) — RAW DEBUG
    # =====================================================================

    async def get_klines_df(self, symbol: str, tf="1H", limit=200):

        # TIMEFRAME NOTHING MAPPED: WE SEND TF DIRECTLY AS STRING
        params = {
            "symbol": symbol,
            "granularity": tf,  # ← EXACT STRING (1H / 4H / 1m)
            "limit": limit
        }

        r = await self._request(
            "GET",
            "/api/v2/mix/market/candles",
            params=params,
            auth=False
        )

        LOGGER.error(f"🔍 RAW_CANDLES_RESPONSE {symbol}({tf}) → {r}")

        if "data" not in r or not r["data"]:
            LOGGER.warning(f"⚠️ EMPTY KLINES for {symbol} ({tf}) → RAW={r}")
            return pd.DataFrame()

        try:
            df = pd.DataFrame(
                r["data"],
                columns=["time", "open", "high", "low", "close", "volume"]
            )
            df = df.astype(float)
            df.sort_values("time", inplace=True)
            return df

        except Exception as exc:
            LOGGER.exception(f"❌ PARSE ERROR {symbol} → {exc}")
            return pd.DataFrame()


# =====================================================================
# SINGLETON
# =====================================================================

_client_instance = None

async def get_client(api_key, api_secret, passphrase):
    global _client_instance
    if _client_instance is None:
        _client_instance = BitgetClient(api_key, api_secret, passphrase)
    return _client_instance
