"""
kucoin_utils.py — robustifié
- fetch_all_symbols(limit): USDT-M, triés par turnover
- fetch_klines(...): granularité en minutes, parsing 6/7 colonnes
- get_contract_info(symbol): lotSize, multiplier, tickSize, max/min, turnover
"""
import logging
import time
from typing import List, Optional, Dict, Any

import pandas as pd
import requests

LOGGER = logging.getLogger(__name__)

BASE = "https://api-futures.kucoin.com"
ACTIVE_ENDPOINT = "/api/v1/contracts/active"
KLINE_ENDPOINT = "/api/v1/kline/query"

_CONTRACTS_CACHE: Dict[str, Dict[str, Any]] = {}
_CONTRACTS_TS = 0.0
_CONTRACTS_TTL = 600.0  # 10min


# ---------------------------
# HTTP util
# ---------------------------
def _get(url: str, params=None, retries: int = 3, timeout: int = 12):
    last_err = None
    for i in range(retries):
        try:
            r = requests.get(url, params=params, timeout=timeout)
            r.raise_for_status()
            return r.json()
        except Exception as e:
            last_err = e
            LOGGER.warning("GET %s failed (%s) try %d/%d", url, e, i + 1, retries)
            time.sleep(0.5 * (2 ** i))
    LOGGER.error("GET %s failed after retries: %s", url, last_err)
    return {}


# ---------------------------
# Symbols & Contracts
# ---------------------------
def _is_usdt_futures(contract: dict) -> bool:
    sym = str(contract.get("symbol", "")).upper()
    settle = (contract.get("settleCurrency") or "").upper()
    return settle == "USDT" or sym.endswith("USDTM") or sym.endswith("USDM")


def _load_contracts(force: bool = False) -> Dict[str, Dict[str, Any]]:
    global _CONTRACTS_CACHE, _CONTRACTS_TS
    now = time.time()
    if (not force) and _CONTRACTS_CACHE and (now - _CONTRACTS_TS < _CONTRACTS_TTL):
        return _CONTRACTS_CACHE

    data = _get(BASE + ACTIVE_ENDPOINT)
    items = data.get("data") or []
    cache = {}
    for c in items:
        try:
            sym = str(c.get("symbol") or "").strip()
            if not sym:
                continue
            cache[sym] = {
                "symbol": sym,
                # valeurs clés pour sizing & arrondis
                "lotSize": int(c.get("lotSize") or 1),           # unité : lots (entier)
                "multiplier": float(c.get("multiplier") or 1.0), # base-coin par lot (ex XBTUSDTM = 0.001 BTC/lot)
                "tickSize": float(c.get("tickSize") or 0.01),    # pas minimal de prix
                "maxOrderQty": float(c.get("maxOrderQty") or 1e12),
                "minPrice": float(c.get("minPrice") or 0.0),
                "maxPrice": float(c.get("maxPrice") or 9e12),
                "turnoverOf24h": float(c.get("turnoverOf24h") or 0.0),
            }
        except Exception as e:
            LOGGER.debug("Skip contract parse error: %s", e)
    if cache:
        _CONTRACTS_CACHE = cache
        _CONTRACTS_TS = now
        LOGGER.info("Cached %d contracts metadata", len(cache))
    return _CONTRACTS_CACHE


def get_contract_info(symbol: str) -> Dict[str, Any]:
    """
    Retourne le dict metadata du contrat (lotSize, multiplier, tickSize, ...).
    """
    contracts = _load_contracts()
    return contracts.get(symbol, {})


def fetch_all_symbols(limit: Optional[int] = None) -> List[str]:
    """
    USDT-M triés par turnover (desc).
    """
    contracts = _load_contracts()
    rows = []
    for c in contracts.values():
        if not _is_usdt_futures(c):
            continue
        rows.append((c["symbol"], c["turnoverOf24h"]))
    if not rows:
        LOGGER.warning("No contracts after filter — fallback static")
        return ["XBTUSDTM", "ETHUSDTM", "SOLUSDTM", "BNBUSDTM"]
    rows.sort(key=lambda x: x[1], reverse=True)
    symbols = [r[0] for r in rows]
    if limit:
        symbols = symbols[:limit]
    LOGGER.info("Fetched %d futures symbols (USDT-M)", len(symbols))
    return symbols


# ---------------------------
# Klines
# ---------------------------
def _granularity(interval: str) -> int:
    mapping = {"1m": 1, "3m": 3, "5m": 5, "15m": 15, "30m": 30, "1h": 60, "2h": 120, "4h": 240, "8h": 480, "12h": 720, "1d": 1440, "1w": 10080}
    return mapping.get(interval, 60)


def _parse_kline_rows(raw):
    if not raw:
        return pd.DataFrame(columns=["time", "open", "high", "low", "close", "volume"])
    df = pd.DataFrame(raw)
    if df.shape[1] == 7:
        df.columns = ["time", "open", "close", "high", "low", "volume", "turnover"]
        out = df[["time", "open", "high", "low", "close", "volume"]].copy()
    elif df.shape[1] == 6:
        df.columns = ["time", "c1", "c2", "c3", "c4", "volume"]
        testA = df.rename(columns={"c1": "open", "c2": "close", "c3": "high", "c4": "low"})
        if (testA["high"] >= testA["low"]).all():
            out = testA[["time", "open", "high", "low", "close", "volume"]].copy()
        else:
            testB = df.rename(columns={"c1": "open", "c2": "high", "c3": "low", "c4": "close"})
            out = testB[["time", "open", "high", "low", "close", "volume"]].copy()
    else:
        cols = ["time", "open", "close", "high", "low", "volume"][: df.shape[1]]
        df.columns = cols
        out = df.reindex(columns=["time", "open", "high", "low", "close", "volume"], fill_value=0)
    for c in ["time", "open", "high", "low", "close", "volume"]:
        out[c] = out[c].astype(float)
    return out.sort_values("time").reset_index(drop=True)


def fetch_klines(symbol: str, interval="1h", limit=200) -> pd.DataFrame:
    url = BASE + KLINE_ENDPOINT
    params = {"symbol": symbol, "granularity": _granularity(interval), "limit": limit}
    data = _get(url, params=params)
    raw = data.get("data") or []
    if not raw:
        LOGGER.warning("No kline data for %s", symbol)
        return pd.DataFrame(columns=["time", "open", "high", "low", "close", "volume"])
    try:
        return _parse_kline_rows(raw)
    except Exception as e:
        LOGGER.exception("Failed to parse klines for %s: %s", symbol, e)
        return pd.DataFrame(columns=["time", "open", "high", "low", "close", "volume"])
