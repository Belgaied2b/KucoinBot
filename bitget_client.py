# =====================================================================
# bitget_client.py — Bitget API FIXED (2025)
# Candles V1 (symbol = BTCUSDT, no suffix)
# Contracts V2
# =====================================================================

from __future__ import annotations
import aiohttp, asyncio, time, hmac, base64, hashlib, json, logging, pandas as pd
from typing import Any, Dict, Optional, List

LOGGER = logging.getLogger(__name__)

# =====================================================================
# RETRY ENGINE
# =====================================================================

async def _async_retry(fn, retries=5, delay=0.35):
    for attempt in range(retries + 1):
        try:
            return await fn()
        except Exception:
            if attempt >= retries:
                raise
            await asyncio.sleep(delay * (1.7 ** attempt))

# =====================================================================
# SYMBOL NORMALIZATION
# =====================================================================

def normalize_symbol(sym: str) -> str:
    """
    Convert every format → BTCUSDT
    """
    if not sym:
        return ""

    s = sym.upper().replace("-", "")
    s = s.replace("USDTM", "USDT").replace("USDTSWAP", "USDT")

    if s.startswith("XBT"):
        s = s.replace("XBT", "BTC")

    return s  # <- FINAL correct symbol for both V1 & V2

# =====================================================================
# TIMEFRAME MAP (V1)
# =====================================================================

TF_MAP = {
    "1H": 3600,
    "4H": 14400,
    "1D": 86400,
    "30M": 1800,
    "15M": 900,
    "5M": 300,
    "1M": 60,
}

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

    async def _ensure_session(self):
        if self.session is None or self.session.closed:
            timeout = aiohttp.ClientTimeout(total=25)
            self.session = aiohttp.ClientSession(timeout=timeout)

    def _sign(self, ts, method, path, query, body):
        msg = f"{ts}{method}{path}{query}{body}"
        mac = hmac.new(self.api_secret, msg.encode(), hashlib.sha256).digest()
        return base64.b64encode(mac).decode()

    async def _request(self, method, path, *, params=None, data=None, auth=True):
        await self._ensure_session()

        params = params or {}
        data = data or {}

        query = ""
        if params:
            query = "?" + "&".join(f"{k}={v}" for k, v in params.items())

        body = json.dumps(data, separators=(",", ":")) if data else ""
        url = self.BASE + path + query

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

            async with self.session.request(method.upper(), url, headers=headers, data=body or None) as resp:
                txt = await resp.text()

                try:
                    return json.loads(txt)
                except:
                    LOGGER.error(f"❌ JSON ERROR: {txt}")
                    return {"code": "99999", "msg": "json_error", "raw": txt}

        return await _async_retry(_do)

    # =====================================================================
    # CONTRACTS (V2)
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

        if "data" not in r:
            LOGGER.error(f"❌ CONTRACT ERROR: {r}")
            return []

        symbols = [normalize_symbol(c["symbol"]) for c in r["data"]]

        LOGGER.info(f"📈 Loaded {len(symbols)} symbols from Bitget Futures")

        self._contracts_cache = symbols
        self._contracts_ts = now
        return symbols

    # =====================================================================
    # CANDLES (V1) — FINAL FIX (NO SUFFIX)
    # =====================================================================

    async def get_klines_df(self, symbol: str, tf="1H", limit=200):
        gran = TF_MAP.get(tf.upper())
        if gran is None:
            LOGGER.error(f"❌ INVALID TF {tf}")
            return pd.DataFrame()

        sym = normalize_symbol(symbol)  # <- correct

        r = await self._request(
            "GET",
            "/api/mix/v1/market/candles",
            params={"symbol": sym, "granularity": gran, "limit": limit},
            auth=False,
        )

        if "data" not in r or not r["data"]:
            LOGGER.warning(f"⚠️ EMPTY KLINES for {sym} ({tf})")
            return pd.DataFrame()

        try:
            df = pd.DataFrame(
                r["data"],
                columns=["time", "open", "high", "low", "close", "volume"],
            )
            df = df.astype(float)
            df.sort_values("time", inplace=True)
            return df.reset_index(drop=True)

        except Exception as e:
            LOGGER.exception(f"❌ PARSE ERROR for {symbol}: {e}")
            return pd.DataFrame()

# =====================================================================
# SINGLETON
# =====================================================================

_client_instance: Optional[BitgetClient] = None

async def get_client(key, secret, passphrase):
    global _client_instance
    if _client_instance is None:
        _client_instance = BitgetClient(key, secret, passphrase)
    return _client_instance
