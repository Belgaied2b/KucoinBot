# =====================================================================
# bitget_client.py — Bitget Futures API v2 (2025)
# Ultra stable — Compatible scanner / trader / signal analyzer
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
# RETRY ENGINE
# =====================================================================

async def _async_backoff_retry(fn, retries=4, delay=0.35, exc=(Exception,)):
    for attempt in range(retries + 1):
        try:
            return await fn()
        except exc:
            if attempt >= retries:
                raise
            await asyncio.sleep(delay * (2 ** attempt))


# =====================================================================
# Bitget Client V2
# =====================================================================

class BitgetClient:
    BASE = "https://api.bitget.com"

    def __init__(self, api_key: str, api_secret: str, passphrase: str):
        self.api_key = api_key
        self.api_secret = api_secret.encode()
        self.passphrase = passphrase

        self.session: Optional[aiohttp.ClientSession] = None

        # Contract cache
        self._contracts = None
        self._contracts_ts = 0

    # ------------------------------------------------------------
    async def _ensure_session(self):
        if self.session is None or self.session.closed:
            timeout = aiohttp.ClientTimeout(total=20)
            self.session = aiohttp.ClientSession(timeout=timeout)

    # ------------------------------------------------------------
    def _sign(self, timestamp: str, method: str, path: str, body: str = "") -> str:
        msg = f"{timestamp}{method}{path}{body}"
        mac = hmac.new(self.api_secret, msg.encode(), hashlib.sha256).digest()
        return base64.b64encode(mac).decode()

    # ------------------------------------------------------------
    async def _request(
        self,
        method: str,
        path: str,
        *,
        body: Dict[str, Any] = None,
        need_auth: bool = False,
        retries: int = 3,
    ) -> Dict[str, Any]:

        await self._ensure_session()

        body_json = json.dumps(body or {}, separators=(",", ":"))

        async def _do():

            ts = str(int(time.time() * 1000))
            headers = {"Content-Type": "application/json"}

            if need_auth:
                sig = self._sign(ts, method.upper(), path, body_json)
                headers.update({
                    "ACCESS-KEY": self.api_key,
                    "ACCESS-SIGN": sig,
                    "ACCESS-TIMESTAMP": ts,
                    "ACCESS-PASSPHRASE": self.passphrase,
                })

            async with self.session.request(
                method.upper(),
                self.BASE + path,
                headers=headers,
                data=body_json if body else None
            ) as resp:
                text = await resp.text()

                # Retry if 429 or 5xx
                if resp.status == 429 or resp.status >= 500:
                    raise ConnectionError(f"{resp.status}: {text}")

                try:
                    js = json.loads(text)
                except:
                    return {"ok": False, "raw": text}

                return {
                    "ok": js.get("code") == "00000",
                    "data": js.get("data"),
                    "raw": js,
                }

        return await _async_backoff_retry(_do, retries=retries)

    # =====================================================================
    # MARKET DATA (Futures USDT)
    # =====================================================================

    async def get_all_contracts(self) -> list:
        """
        Official Bitget v2 futures contract list.
        """
        now = time.time()
        if self._contracts and now - self._contracts_ts < 300:
            return self._contracts

        r = await self._request(
            "GET",
            "/api/v2/mix/market/contracts?productType=USDT-FUTURES",
            need_auth=False,
        )

        if not r.get("ok"):
            LOGGER.error(f"📡 RAW CONTRACTS RESPONSE: {r}")
            self._contracts = []
        else:
            self._contracts = r.get("data", []) or []

        self._contracts_ts = now
        return self._contracts

    # -----------------------------------------------------------------

    async def get_klines_df(self, symbol: str, tf: str, limit: int = 200) -> pd.DataFrame:
        """
        Returns OHLCV DataFrame formatted for analyze_signal.
        """

        r = await self._request(
            "GET",
            f"/api/v2/mix/market/candles?symbol={symbol}&granularity={tf}&limit={limit}",
            need_auth=False,
        )

        raw = r.get("data")
        if not r.get("ok") or not raw:
            return pd.DataFrame()

        try:
            df = pd.DataFrame(
                raw,
                columns=["time", "open", "high", "low", "close", "volume"]
            )
            df = df.astype({
                "time": float,
                "open": float,
                "high": float,
                "low": float,
                "close": float,
                "volume": float,
            })
            return df.sort_values("time").reset_index(drop=True)
        except Exception:
            LOGGER.exception(f"Klines parse failed for {symbol}")
            return pd.DataFrame()

    # =====================================================================
    # POSITION / EXECUTION LAYER (Used by bitget_trader.py)
    # =====================================================================

    async def place_order(
        self,
        symbol: str,
        side: str,
        price: float,
        size: float,
        order_type="limit",
    ):
        """
        Core entry function — V2 Bitget
        """
        body = {
            "symbol": symbol,
            "side": side.lower(),       # buy / sell
            "orderType": order_type,    # limit / market
            "price": str(price),
            "size": str(size),
            "marginCoin": "USDT",
        }

        return await self._request(
            "POST",
            "/api/v2/mix/order/place-order",
            body=body,
            need_auth=True,
        )

    # -----------------------------------------------------------------

    async def close(self):
        if self.session and not self.session.closed:
            await self.session.close()


# =====================================================================
# GLOBAL SINGLETON
# =====================================================================

_client_cache: Optional[BitgetClient] = None


async def get_client(api_key: str, api_secret: str, passphrase: str) -> BitgetClient:
    global _client_cache
    if _client_cache is None:
        _client_cache = BitgetClient(api_key, api_secret, passphrase)
    return _client_cache
