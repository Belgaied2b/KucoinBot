# =====================================================================
# bitget_client.py — Bitget API v2/v3 (2025) — FINAL STABLE VERSION
# =====================================================================
# • Contrats Futures : /api/v2/mix/market/contracts
# • Klines Futures  : /api/v3/market/candles  (USDT-FUTURES)
# • Symboles       : BTCUSDT, ETHUSDT… (aucun suffixe)
# • Logs détaillés en cas d'erreur / data vide
# • Compatible scanner.py / analyze_signal.py / bitget_trader.py
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

async def _async_retry(fn, retries: int = 5, base_delay: float = 0.35):
    for attempt in range(retries + 1):
        try:
            return await fn()
        except Exception as exc:
            if attempt >= retries:
                raise
            await asyncio.sleep(base_delay * (1.8 ** attempt))


# =====================================================================
# SYMBOL NORMALISATION
# =====================================================================

def normalize_symbol(sym: str) -> str:
    """
    Normalise BTC-USDT, BTCUSDTM, XBTUSDT → BTCUSDT
    On garde ensuite exactement ce format pour Bitget v3.
    """
    if not sym:
        return ""

    s = sym.upper().replace("-", "")
    s = s.replace("USDTM", "USDT").replace("USDTSWAP", "USDT")

    if s.startswith("XBT"):
        s = s.replace("XBT", "BTC")

    return s


# =====================================================================
# CLIENT CORE
# =====================================================================

class BitgetClient:
    BASE = "https://api.bitget.com"

    def __init__(self, api_key: str, api_secret: str, api_passphrase: str):
        self.api_key = api_key
        self.api_secret = api_secret.encode()
        self.api_passphrase = api_passphrase

        self.session: Optional[aiohttp.ClientSession] = None

        # Cache des contrats
        self._contracts_cache: Optional[List[str]] = None
        self._contracts_ts: float = 0.0

    # ---------------------------------------------------------------
    async def _ensure_session(self):
        if self.session is None or self.session.closed:
            timeout = aiohttp.ClientTimeout(total=25)
            self.session = aiohttp.ClientSession(timeout=timeout)

    # ---------------------------------------------------------------
    def _sign(self, ts: str, method: str, path: str, query: str, body: str) -> str:
        msg = f"{ts}{method}{path}{query}{body}"
        mac = hmac.new(self.api_secret, msg.encode(), hashlib.sha256).digest()
        return base64.b64encode(mac).decode()

    # ---------------------------------------------------------------
    async def _request(
        self,
        method: str,
        path: str,
        *,
        params: Dict[str, Any] | None = None,
        data: Dict[str, Any] | None = None,
        auth: bool = True,
    ) -> Dict[str, Any]:
        """
        Wrapper HTTP unique pour tous les endpoints Bitget.
        Retourne toujours un dict (réponse JSON parseée).
        """
        await self._ensure_session()

        params = params or {}
        data = data or {}

        # Querystring
        if params:
            query = "?" + "&".join(f"{k}={v}" for k, v in params.items())
        else:
            query = ""

        url = self.BASE + path + query
        body = json.dumps(data, separators=(",", ":")) if data else ""

        async def _do():
            ts = str(int(time.time() * 1000))
            headers: Dict[str, str] = {}

            if auth:
                sig = self._sign(ts, method.upper(), path, query, body)
                headers = {
                    "ACCESS-KEY": self.api_key,
                    "ACCESS-SIGN": sig,
                    "ACCESS-TIMESTAMP": ts,
                    "ACCESS-PASSPHRASE": self.api_passphrase,
                    "Content-Type": "application/json",
                }

            async with self.session.request(
                method.upper(), url, headers=headers, data=body or None
            ) as resp:
                txt = await resp.text()

                # Log si HTTP pas 200
                if resp.status != 200:
                    LOGGER.error(
                        f"HTTP {resp.status} {method} {path} params={params} body={data} raw={txt}"
                    )

                try:
                    js = json.loads(txt)
                except Exception:
                    LOGGER.error(
                        f"❌ JSON PARSE ERROR {method} {path} params={params} raw={txt}"
                    )
                    return {"code": "HTTP", "msg": f"http_status_{resp.status}", "raw": txt}

                # Petit log debug quand code != 00000
                code = js.get("code")
                if code and code != "00000":
                    LOGGER.warning(
                        f"⚠️ API CODE {code} on {method} {path} params={params} → {js}"
                    )

                return js

        return await _async_retry(_do)

    # =====================================================================
    # CONTRACTS FUTURES (API v2)
    # =====================================================================

    async def get_contracts_list(self) -> List[str]:
        """
        Récupère la liste des symboles USDT-FUTURES (BTCUSDT, ETHUSDT…)
        via l'API v2 mix/market/contracts.
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

        if not isinstance(r, dict) or r.get("code") != "00000" or "data" not in r:
            LOGGER.error(f"❌ CONTRACTS ERROR: {r}")
            return []

        symbols: List[str] = []
        for c in r["data"]:
            sym_raw = c.get("symbol")
            if not sym_raw:
                continue
            sym = normalize_symbol(sym_raw)
            symbols.append(sym)

        # Nettoyage doublons
        symbols = sorted(list(set(symbols)))

        LOGGER.info(f"📈 Loaded {len(symbols)} symbols from Bitget Futures")

        self._contracts_cache = symbols
        self._contracts_ts = now
        return symbols

    # =====================================================================
    # KLINES FUTURES (API v3)
    # =====================================================================

    async def get_klines_df(
        self,
        symbol: str,
        tf: str = "1H",
        limit: int = 200,
    ) -> pd.DataFrame:
        """
        Récupère les chandeliers via /api/v3/market/candles
        category=USDT-FUTURES, symbol=BTCUSDT, interval=1H/4H...
        """
        sym = normalize_symbol(symbol)
        interval = tf  # on passe "1H", "4H" etc. directement

        params = {
            "category": "USDT-FUTURES",
            "symbol": sym,
            "interval": interval,
            "type": "market",
            "limit": str(limit),
        }

        r = await self._request(
            "GET",
            "/api/v3/market/candles",
            params=params,
            auth=False,
        )

        if not isinstance(r, dict) or r.get("code") != "00000":
            LOGGER.warning(
                f"⚠️ EMPTY/ERROR KLINES for {sym} ({tf}) → RAW={r}"
            )
            return pd.DataFrame()

        data = r.get("data") or []
        if not data:
            LOGGER.warning(
                f"⚠️ EMPTY KLINES for {sym} ({tf}) → RAW={r}"
            )
            return pd.DataFrame()

        try:
            # data = [[ts, open, high, low, close, volume, turnover], ...]
            df_raw = pd.DataFrame(data)

            # On garde les 6 premières colonnes : ts, o, h, l, c, v
            df = df_raw.iloc[:, :6].copy()
            df.columns = ["time", "open", "high", "low", "close", "volume"]

            # Cast → float
            for col in ["time", "open", "high", "low", "close", "volume"]:
                df[col] = df[col].astype(float)

            # Ordre chronologique (ancien → récent)
            df = df.sort_values("time").reset_index(drop=True)

            return df

        except Exception as exc:
            LOGGER.exception(
                f"❌ PARSE ERROR KLINES {sym} ({tf}) → exc={exc} RAW={r.get('data')}"
            )
            return pd.DataFrame()

    # =====================================================================
    # CLOSE SESSION
    # =====================================================================

    async def close(self):
        if self.session and not self.session.closed:
            await self.session.close()


# =====================================================================
# SINGLETON
# =====================================================================

_client_instance: Optional[BitgetClient] = None

async def get_client(api_key: str, api_secret: str, api_passphrase: str) -> BitgetClient:
    global _client_instance
    if _client_instance is None:
        _client_instance = BitgetClient(api_key, api_secret, api_passphrase)
    return _client_instance
