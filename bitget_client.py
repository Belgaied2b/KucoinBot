# =====================================================================
# bitget_client.py — Bitget API v2 (2025) — FINAL GRANULARITY FIX
# =====================================================================

from __future__ import annotations
import aiohttp, asyncio, time, hmac, base64, hashlib, json, logging, pandas as pd
from typing import Any, Dict, Optional, List

LOGGER = logging.getLogger(__name__)

# =====================================================================
# RETRY
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
# SYMBOL NORMALISATION
# =====================================================================

def normalize_symbol(sym: str) -> str:
    if not sym:
        return ""
    s = sym.upper().replace("-", "")
    s = s.replace("USDTM", "USDT").replace("USDTSWAP", "USDT")
    if s.startswith("XBT"):
        s = s.replace("XBT", "BTC")
    return s

def format_symbol(sym: str) -> str:
    return normalize_symbol(sym)

# =====================================================================
# TIMEFRAME MAP — OBLIGATOIRE POUR BITGET
# =====================================================================

TF_MAP = {
    "1H": 3600,
    "4H": 14400,
    "1m": 60,
    "5m": 300,
    "15m": 900,
    "30m": 1800,
    "1D": 86400,
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
            timeout = aiohttp.ClientTimeout(total=30)
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
                    "Content-Type": "application/json",
                }

            async with self.session.request(method.upper(), url, headers=headers, data=body or None) as resp:
                txt = await resp.text()
                try:
                    return json.loads(txt)
                except:
                    LOGGER.error(f"JSON ERROR: {txt}")
                    return {"code": "99999", "msg": "json error", "raw": txt}

        return await _async_backoff_retry(_do)

    # =====================================================================
    # CONTRATS (v2)
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
            LOGGER.error(f"CONTRACT ERROR: {r}")
            return []

        symbols = [format_symbol(c["symbol"]) for c in r["data"]]

        LOGGER.info(f"📈 Loaded {len(symbols)} symbols from Bitget v2")

        self._contracts_cache = symbols
        self._contracts_ts = now
        return symbols

    # =====================================================================
    # KLNIES (v2) — FIX GRANULARITY
    # =====================================================================

    async def get_klines_df(self, symbol: str, tf="1H", limit=200):
        sym = format_symbol(symbol)

        gran = TF_MAP.get(tf.upper())
        if gran is None:
            LOGGER.error(f"❌ UNKNOWN TF {tf} — valid: {list(TF_MAP.keys())}")
            return pd.DataFrame()

        r = await self._request(
            "GET",
            "/api/v2/mix/market/candles",
            params={"symbol": sym, "granularity": gran, "limit": limit},
            auth=False,
        )

        if "data" not in r or not r["data"]:
            LOGGER.warning(f"⚠️ EMPTY KLNIES for {sym} ({tf})")
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
            LOGGER.exception(f"PARSE ERROR {symbol} → {e}")
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
