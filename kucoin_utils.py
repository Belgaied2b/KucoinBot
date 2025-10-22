"""
kucoin_utils.py — version robuste (granularité en minutes + tri par turnover)
- Récupère tous les contrats Futures USDT-M (settleCurrency=USDT ou suffixe USDTM/USDM)
- Trie par turnover 24h (desc) pour prioriser la liquidité
- Granularité KuCoin Futures = minutes (ex: 60 pour 1h)
- Retries + timeouts + logs explicites
"""
import logging
import time
import requests
import pandas as pd
from typing import List, Optional

LOGGER = logging.getLogger(__name__)

BASE = "https://api-futures.kucoin.com"
ACTIVE_ENDPOINT = "/api/v1/contracts/active"
KLINE_ENDPOINT = "/api/v1/kline/query"

def _get(url: str, params=None, retries: int = 3, timeout: int = 12):
    last_err = None
    for i in range(retries):
        try:
            r = requests.get(url, params=params, timeout=timeout)
            r.raise_for_status()
            return r.json()
        except Exception as e:
            last_err = e
            LOGGER.warning("GET %s failed (%s) try %d/%d", url, e, i+1, retries)
            time.sleep(0.5 * (2**i))
    LOGGER.error("GET %s failed after retries: %s", url, last_err)
    return {}

def _is_usdt_futures(contract: dict) -> bool:
    """
    On accepte:
    - settleCurrency == 'USDT' (usdt-margined)
    - OU symbole se terminant par USDTM / USDM
    """
    sym = str(contract.get("symbol", "")).upper()
    settle = (contract.get("settleCurrency") or "").upper()
    return (
        settle == "USDT"
        or sym.endswith("USDTM")
        or sym.endswith("USDM")
    )

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

    # Tri par turnover 24h décroissant = liquidité d'abord
    rows.sort(key=lambda x: x[1], reverse=True)
    symbols = [r[0] for r in rows]
    if limit:
        symbols = symbols[:limit]

    LOGGER.info("Fetched %d futures symbols (USDT-M)", len(symbols))
    return symbols

def _granularity(interval: str) -> int:
    """
    KuCoin Futures attend une granularité en MINUTES.
    Valeurs valides typiques: 1,3,5,15,30,60,120,240,480,720,1440,10080.
    """
    mapping = {"1m":1, "3m":3, "5m":5, "15m":15, "30m":30, "1h":60, "2h":120, "4h":240, "8h":480, "12h":720, "1d":1440, "1w":10080}
    return mapping.get(interval, 60)  # défaut: 1h

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
        return pd.DataFrame(columns=["time","open","high","low","close","volume"])
    # KuCoin renvoie [ts, open, close, high, low, vol] OU [ts, open, high, low, close, vol] selon endpoints.
    # Pour Futures, ordre officiel: [ts, open, close, high, low, vol] — on harmonise :
    # Si longueur 6, on détecte par comparaison open/close/high/low.
    df = pd.DataFrame(raw)
    if df.shape[1] == 6:
        # Tentative d'inférence de colonnes (la plupart des réponses futures: [ts, open, close, high, low, vol])
        df.columns = ["time","open","close","high","low","volume"]
        # Si jamais high < low partout, on swappe avec close/ordre alternatif (sécurité basique)
        if (df["high"] < df["low"]).all():
            df.columns = ["time","open","high","low","close","volume"]
    else:
        # fallback
        df = pd.DataFrame(raw, columns=["time","open","high","low","close","volume"])
    # Types
    for c in ["open","high","low","close","volume"]:
        df[c] = df[c].astype(float)
    return df
