# =====================================================================
# bitget_client.py — DIAGNOSTIC MODE (PRINT RAW API RESPONSES)
# =====================================================================

from __future__ import annotations
import aiohttp, asyncio, time, hmac, base64, hashlib, json, logging, pandas as pd
from typing import Any, Dict, Optional, List

LOGGER = logging.getLogger(__name__)

# =====================================================================
# RETRY ENGINE
# =====================================================================

async def _async_retry(fn, retries=4, delay=0.25):
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
    if not sym:
        return ""
    s = sym.upper().replace("-", "")
    s = s.replace("USDTM", "USDT").replace("USDTSWAP", "USDT")
    if s.startswith("XBT"):
        s = s.replace("XBT", "BTC")
    return s

# =====================================================================
# TIMEFRAME MAP
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

    def __init__(self, key: str, secret: str, passphrase: str):
        self.api_key = key
        self.api_secret = secret.encode()
        self.api_passphrase = passphrase
        self.session: Optional[aiohttp.ClientSession] = None

        self._contracts_cache = None
        self._contracts_ts = 0

    async def _ensure_session(self):
        if self.session is None or self.session.closed:
            self.session = aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=20))

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
            query = "?" + "&".join([f"{k}={v}" for k, v in params.items()])

        body = json.dumps(data) if data else ""
        url = self.BASE + path + query

        async def _do():
            ts = str(int(time.time() * 1000))
            headers = {}

            if auth:
                sig = self._sign(ts, method, path, query, body)
                headers = {
                    "ACCESS-KEY": self.api_key,
                    "ACCESS-SIGN": sig,
                    "ACCESS-TIMESTAMP": ts,
                    "ACCESS-PASSPHRASE": self.api_passphrase,
                    "Content-Type": "application/json"
                }

            async with self.session.request(method, url, headers=headers, data=body or None) as resp:
                txt = await resp.text()
                try:
                    js = json.loads(txt)
                except:
                    LOGGER.error(f"❌ JSON PARSE ERROR RAW={txt}")
                    return {"raw": txt, "error": "json_parse"}

                return js

        return await _async_retry(_do)

    # =====================================================================
    # CONTRACT LIST (OK)
    # =====================================================================

    async def get_contracts_list(self) -> List[str]:
        r = await self._request(
            "GET",
            "/api/v2/mix/market/contracts",
            params={"productType": "USDT-FUTURES"},
            auth=False,
        )

        if "data" not in r:
            LOGGER.error(f"❌ CONTRACT ERROR RAW={r}")
            return []

        symbols = [normalize_symbol(c["symbol"]) for c in r["data"]]
        LOGGER.info(f"📈 Loaded {len(symbols)} symbols from Bitget Futures")
        return symbols

    # =====================================================================
    # KLINES (DIAGNOSTIC MODE)
    # =====================================================================

    async def get_klines_df(self, symbol: str, tf="1H", limit=200):
        gran = TF_MAP.get(tf.upper())
        if gran is None:
            LOGGER.error(f"❌ INVALID TF {tf}")
            return pd.DataFrame()

        sym = normalize_symbol(symbol)

        r = await self._request(
            "GET",
            "/api/mix/v1/market/candles",
            params={
                "symbol": sym,
                "granularity": gran,
                "limit": limit,
            },
            auth=False
        )

        # 🔥 NOUVEAU : LOG COMPLET DE LA RÉPONSE
        LOGGER.error(f"🔍 RAW_CANDLES_RESPONSE {sym}({tf}) → {r}")

        if "data" not in r or not r["data"]:
            LOGGER.warning(f"⚠️ EMPTY KLINES for {sym} ({tf})")
            return pd.DataFrame()

        try:
            df = pd.DataFrame(
                r["data"],
                columns=["time", "open", "high", "low", "close", "volume"]
            )
            df = df.astype(float)
            df.sort_values("time", inplace=True)
            return df.reset_index(drop=True)

        except Exception as e:
            LOGGER.exception(f"❌ PARSE ERROR {symbol}: {e}")
            return pd.DataFrame()


# =====================================================================
# SINGLETON
# =====================================================================

_client_instance = None

async def get_client(key, secret, passphrase):
    global _client_instance
    if _client_instance is None:
        _client_instance = BitgetClient(key, secret, passphrase)
    return _client_instance
