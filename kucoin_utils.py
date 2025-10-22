"""
kucoin_utils.py — version robuste
- Récupère tous les contrats Futures USDT-M (settleCurrency=USDT ou suffixe USDTM/USDM)
- Retries + timeouts + logs explicites
- Fallback si l'API renvoie vide
"""
import logging
import time
import requests
import pandas as pd

LOGGER = logging.getLogger(__name__)

BASE = "https://api-futures.kucoin.com"
ACTIVE_ENDPOINT = "/api/v1/contracts/active"

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
    - OU symbole se terminant par USDTM / USDM (cas fréquents KuCoin)
    """
    sym = str(contract.get("symbol", "")).upper()
    settle = (contract.get("settleCurrency") or "").upper()
    return (
        settle == "USDT"
        or sym.endswith("USDTM")
        or sym.endswith("USDM")
    )

def fetch_all_symbols() -> list[str]:
    """
    Retourne une liste de symboles Futures tradables (strings).
    En cas de réponse vide, fournit un fallback minimal pour ne pas bloquer le scan.
    """
    url = BASE + ACTIVE_ENDPOINT
    data = _get(url)
    items = data.get("data") or []
    if not items:
        LOGGER.error("contracts/active renvoie vide — fallback statique")
        return ["XBTUSDTM", "ETHUSDTM", "SOLUSDTM", "BNBUSDTM"]

    # Certains champs (status/enableTrading) ne sont pas uniformes
    # On ne filtre que sur l’aspect USDT-M, le reste se fera à l’analyse.
    symbols = []
    for c in items:
        try:
            if _is_usdt_futures(c):
                sym = str(c.get("symbol") or "").strip()
                if sym:
                    symbols.append(sym)
        except Exception as e:
            LOGGER.debug("Ignore contract parse error: %s", e)

    # Dédup + tri pour stabilité des logs
    symbols = sorted(set(symbols))

    if not symbols:
        LOGGER.warning("Aucun symbole après filtrage — fallback statique")
        return ["XBTUSDTM", "ETHUSDTM", "SOLUSDTM", "BNBUSDTM"]

    LOGGER.info("Fetched %d futures symbols (USDT-M)", len(symbols))
    return symbols

def _granularity(interval: str) -> int:
    return {"1m":60, "5m":300, "15m":900, "1h":3600, "4h":14400, "1d":86400}.get(interval, 3600)

def fetch_klines(symbol: str, interval="1h", limit=200) -> pd.DataFrame:
    """
    Retourne un DataFrame colonnes: time, open, high, low, close, volume
    """
    url = BASE + "/api/v1/kline/query"
    params = {"symbol": symbol, "granularity": _granularity(interval), "limit": limit}
    data = _get(url, params=params)
    raw = data.get("data") or []
    if not raw:
        LOGGER.warning("No kline data for %s", symbol)
        return pd.DataFrame(columns=["time","open","high","low","close","volume"])
    df = pd.DataFrame(raw, columns=["time","open","high","low","close","volume"]).astype(float)
    return df
