# =====================================================================
# bitget_client.py — Desk Lead Edition (2025, API v2)
# Ultra-compatible with scanner.py, analyze_signal.py, bitget_trader.py
# =====================================================================

from __future__ import annotations
import aiohttp
import asyncio
import time
import hmac
import hashlib
import base64
import json
import logging
import pandas as pd
from typing import Any, Dict, Optional, List

LOGGER = logging.getLogger(__name__)


# =====================================================================
# BACKOFF RETRY
# =====================================================================

async def _retry(fn, retries=4, base=0.25):
    for i in range(retries + 1):
        try:
            return await fn()
        except Exception as e:
            if i >= retries:
                raise
            await asyncio.sleep(base * (2 ** i))


# =====================================================================
# KUCOIN → BITGET SYMBOL NORMALISATION
# =====================================================================

def normalize_symbol(sym: str) -> str:
    """
    Ton bot utilise des symboles type:
        BTCUSDT_UMCBL
    Bitget V2 renvoie:
        BTCUSDT
    Donc on génère automatiquement le suffixe perp:
    """
    s = sym.upper()
    s = s.replace("USDTM", "").replace("-USDTM", "")
    if s.endswith("_UMCBL"):
        return s.replace("_UMCBL", "")  # BTCUSDT
    if s.endswith("_DMCBL"):
        return s.replace("_DMCBL", "")
    return s.replace("_UMCBL", "")


def add_suffix(sym: str) -> str:
    """Ajoute le suffixe perp correct si absent."""
    s = sym.upper()
    if not s.endswith("USDT_UMCBL"):
        if s.endswith("USDT"):
            return s + "_UMCBL"
    return s


# =====================================================================
# BITGET CLIENT (API v2)
# =====================================================================

class BitgetClient:

    BASE = "https://api.bitget.com"

    def __init__(self, api_key: str, api_secret: str, passphrase: str):
        self.key = api_key
        self.secret = api_secret.encode()
        self.passphrase = passphrase
        self.session: Optional[aiohttp.ClientSession] = None

        self.contracts_cache = {"ts": 0, "list": []}

    # --------------------------------------------------------------
    async def _ensure(self):
        if self.session is None or self.session.closed:
            timeout = aiohttp.ClientTimeout(total=20)
            self.session = aiohttp.ClientSession(timeout=timeout)

    # --------------------------------------------------------------
    def _sign(self, ts: str, method: str, path: str, body: str = ""):
        pre = ts + method.upper() + path + body
        mac = hmac.new(self.secret, pre.encode(), hashlib.sha256).digest()
        return base64.b64encode(mac).decode()

    # --------------------------------------------------------------
    async def _request(self, method: str, path: str, *, params=None, data=None, auth=True):
        await self._ensure()

        params = params or {}
        data = data or {}

        query = ""
        if params:
            query = "?" + "&".join(f"{k}={v}" for k, v in params.items())

        url = self.BASE + path + query
        body = json.dumps(data) if data else ""

        async def _do():
            ts = str(int(time.time() * 1000))
            headers = {"Content-Type": "application/json"}

            if auth:
                sig = self._sign(ts, method, path, body)
                headers.update({
                    "ACCESS-KEY": self.key,
                    "ACCESS-SIGN": sig,
                    "ACCESS-TIMESTAMP": ts,
                    "ACCESS-PASSPHRASE": self.passphrase,
                })

            async with self.session.request(method, url, headers=headers, data=body or None) as r:
                txt = await r.text()

                if r.status in (429, 500, 502, 503, 504):
                    raise ConnectionError(f"Retryable: {r.status} {txt}")

                try:
                    js = json.loads(txt)
                except:
                    return {"ok": False, "raw": txt}

                return {
                    "ok": js.get("code") == "00000",
                    "data": js.get("data"),
                    "raw": js,
                    "status": r.status
                }

        return await _retry(_do)

    # =====================================================================
    # GET CONTRACT LIST (API v2)
    # =====================================================================

    async def get_contracts_list(self) -> List[str]:
        """
        API V2 OFFICIELLE :
        GET /api/v2/mix/market/contracts?productType=umcbl
        """
        now = time.time()
        if now - self.contracts_cache["ts"] < 300 and self.contracts_cache["list"]:
            return self.contracts_cache["list"]

        r = await self._request(
            "GET",
            "/api/v2/mix/market/contracts",
            params={"productType": "umcbl"},
            auth=False
        )

        if not r["ok"]:
            LOGGER.error(f"📡 CONTRACT LIST ERROR: {r['raw']}")
            return []

        symbols = []
        for c in r["data"]:
            s = c.get("symbol")
            if not s:
                continue
            # Bitget returns "BTCUSDT" → add suffix
            symbols.append(add_suffix(s))

        LOGGER.info(f"🟢 {len(symbols)} contracts loaded (v2)")

        self.contracts_cache = {"ts": now, "list": symbols}
        return symbols

    # =====================================================================
    # KLINES (OHLCV)
    # =====================================================================

    async def get_klines_df(self, symbol: str, tf: str = "1H", limit: int = 200) -> pd.DataFrame:
        """
        Format Bitget v2:
            /api/v2/mix/market/candles?symbol=BTCUSDT&granularity=1h
        """
        base_symbol = normalize_symbol(symbol)  # BTCUSDT
        suffix_symbol = add_suffix(base_symbol)  # BTCUSDT_UMCBL

        tf_map = {
            "1H": "1h",
            "4H": "4h",
            "5M": "5m",
            "15M": "15m"
        }

        gran = tf_map.get(tf, "1h")

        r = await _retry(lambda: self._request(
            "GET",
            "/api/v2/mix/market/candles",
            params={
                "symbol": base_symbol,
                "granularity": gran,
                "limit": limit
            },
            auth=False
        ))

        raw = r.get("data")
        if not raw:
            return pd.DataFrame()

        # Bitget returns list of arrays:
        # [timestamp, open, high, low, close, volume]
        try:
            df = pd.DataFrame(
                raw,
                columns=["time", "open", "high", "low", "close", "volume"]
            ).astype(float)
            return df.sort_values("time").reset_index(drop=True)
        except Exception:
            LOGGER.error(f"Failed parse OHLCV for {symbol}: {r}")
            return pd.DataFrame()

    # =====================================================================
    async def close(self):
        if self.session and not self.session.closed:
            await self.session.close()


# =====================================================================
# SINGLETON CLIENT
# =====================================================================

_client = None

async def get_client(api_key, api_secret, passphrase) -> BitgetClient:
    global _client
    if _client is None:
        _client = BitgetClient(api_key, api_secret, passphrase)
    return _client
