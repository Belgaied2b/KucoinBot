"""
kucoin_utils.py — robuste (granularité minutes + parsing 6/7 colonnes)
- Récupère tous les contrats Futures USDT-M (settleCurrency=USDT ou suffixe USDTM/USDM)
- Trie par turnover 24h (desc) pour prioriser la liquidité
- Granularité KuCoin Futures = minutes (ex: 60 pour 1h)
- Parsing kline tolérant: 7 colonnes [ts, open, close, high, low, volume, turnover]
  ou 6 colonnes (ordres variables) avec heuristique
"""
import logging
import time
from typing import List, Optional

import pandas as pd
import requests

LOGGER = logging.getLogger(__name__)

BASE = "https://api-futures.kucoin.com"
ACTIVE_ENDPOINT = "/api/v1/contracts/active"
KLINE_ENDPOINT = "/api/v1/kline/query"


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
# Symbols
# ---------------------------
def _is_usdt_futures(contract: dict) -> bool:
    """
    On accepte:
    - settleCurrency == 'USDT' (usdt-margined)
    - OU symbole se terminant par USDTM / USDM
    """
    sym = str(contract.get("symbol", "")).upper()
    settle = (contract.get("settleCurrency") or "").upper()
    return settle == "USDT" or sym.endswith("USDTM") or sym.endswith("USDM")


def fetch_all_symbols(limit: Optional[int] = None) -> List[str]:
    """
    Retourne une liste de symboles Futures USDT-M triés par turnover (desc).
    `limit`: si renseigné, limite le nombre de symboles retournés (ex: 150).
    """
    url = BASE + ACTIVE_ENDPOINT
    data = _get(url)
    items = data.get("data") or []
    if not items:
        LOGGER.error("contracts/active renvoie vide — fallback statique")
        return ["XBTUSDTM", "ETHUSDTM", "SOLUSDTM", "BNBUSDTM"]

    rows = []
    for c in items:
        try:
            if not _is_usdt_futures(c):
                continue
            sym = str(c.get("symbol") or "").strip()
            if not sym:
                continue
            turnover = float(c.get("turnoverOf24h") or 0.0)
            rows.append((sym, turnover))
        except Exception as e:
            LOGGER.debug("Ignore contract parse error: %s", e)

    if not rows:
        LOGGER.warning("Aucun symbole après filtrage — fallback statique")
        return ["XBTUSDTM", "ETHUSDTM", "SOLUSDTM", "BNBUSDTM"]

    rows.sort(key=lambda x: x[1], reverse=True)  # liquidité d’abord
    symbols = [r[0] for r in rows]
    if limit:
        symbols = symbols[:limit]

    LOGGER.info("Fetched %d futures symbols (USDT-M)", len(symbols))
    return symbols


# ---------------------------
# Klines
# ---------------------------
def _granularity(interval: str) -> int:
    """
    KuCoin Futures attend une granularité en MINUTES.
    Valeurs valides typiques: 1,3,5,15,30,60,120,240,480,720,1440,10080.
    """
    mapping = {
        "1m": 1,
        "3m": 3,
        "5m": 5,
        "15m": 15,
        "30m": 30,
        "1h": 60,
        "2h": 120,
        "4h": 240,
        "8h": 480,
        "12h": 720,
        "1d": 1440,
        "1w": 10080,
    }
    return mapping.get(interval, 60)  # défaut 1h


def _parse_kline_rows(raw):
    """
    Gère les formats:
    - 7 colonnes: [ts, open, close, high, low, volume, turnover]  (observé fréquemment)
    - 6 colonnes: ordre parfois [ts, open, high, low, close, volume] ou [ts, open, close, high, low, volume]
    Retourne DataFrame time, open, high, low, close, volume
    """
    if not raw:
        return pd.DataFrame(columns=["time", "open", "high", "low", "close", "volume"])

    # Normalisation en DataFrame brut
    df = pd.DataFrame(raw)

    # Cas 7 colonnes
    if df.shape[1] == 7:
        # Mapping officiel souvent: [ts, open, close, high, low, volume, turnover]
        # On réordonne en [time, open, high, low, close, volume]
        df = df.rename(
            columns={0: "time", 1: "open", 2: "close", 3: "high", 4: "low", 5: "volume", 6: "turnover"}
        )
        out = df[["time", "open", "high", "low", "close", "volume"]].copy()

    # Cas 6 colonnes
    elif df.shape[1] == 6:
        # Deux variantes possibles; on essaie l'ordre [ts, open, close, high, low, volume], sinon on swap.
        df = df.rename(columns={0: "time", 1: "c1", 2: "c2", 3: "c3", 4: "c4", 5: "volume"})
        # Hypothèse A: c1=open, c2=close, c3=high, c4=low
        testA = df.rename(columns={"c1": "open", "c2": "close", "c3": "high", "c4": "low"})
        if (testA["high"] >= testA["low"]).all():
            out = testA[["time", "open", "high", "low", "close", "volume"]].copy()
        else:
            # Hypothèse B: c1=open, c2=high, c3=low, c4=close
            testB = df.rename(columns={"c1": "open", "c2": "high", "c3": "low", "c4": "close"})
            out = testB[["time", "open", "high", "low", "close", "volume"]].copy()
    else:
        # Fallback grossier: si 5+ colonnes, essaye la forme la plus commune
        cols = ["time", "open", "close", "high", "low", "volume"][: df.shape[1]]
        df.columns = cols
        if set(["time", "open", "close", "high", "low"]).issubset(df.columns):
            # Réordonne si possible
            out = df.reindex(columns=["time", "open", "high", "low", "close", "volume"], fill_value=0).copy()
        else:
            return pd.DataFrame(columns=["time", "open", "high", "low", "close", "volume"])

    # Types
    for c in ["time", "open", "high", "low", "close", "volume"]:
        out[c] = out[c].astype(float)

    # Tri temporel ascendant
    out = out.sort_values("time").reset_index(drop=True)
    return out


def fetch_klines(symbol: str, interval="1h", limit=200) -> pd.DataFrame:
    """
    Retourne un DataFrame colonnes: time, open, high, low, close, volume
    """
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
