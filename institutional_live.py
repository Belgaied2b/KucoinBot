"""
Collecte en temps réel des métriques institutionnelles (Open Interest, Funding, CVD/Delta)
avec mapping KuCoin -> Binance Futures + cache des symboles Binance pour éviter les 400.

Principes:
- KuCoin: symboles type 'XBTUSDTM' ; Binance Futures attend 'BTCUSDT'.
- On mappe: strip suffixes USDTM/USDM, alias XBT->BTC, puis on vérifie l'existence via /fapi/v1/exchangeInfo.
- Si symbole introuvable sur Binance -> on retourne un score neutre (0) sans spammer les logs.
- Retry seulement sur 429/5xx ; 4xx (400/404) sont non-retryables.

Dépendances: requests, logging, time
"""
import time
import logging
from typing import Optional, Dict, Any, Set

import requests

LOGGER = logging.getLogger(__name__)

# -------------------------
# Caches
# -------------------------
_CACHE = {}
_TTL_SHORT = 10    # secondes (données live)
_TTL_SYMBOLS = 6 * 3600  # 6h pour la liste des symboles Binance

_BINANCE_FUTURES_SYMBOLS: Set[str] = set()
_BINANCE_FUTURES_LAST_LOAD = 0.0

_ALIAS = {
    "XBT": "BTC",   # KuCoin XBT -> Binance BTC
    # Ajoute d'autres alias au besoin (peu fréquents)
}

# -------------------------
# Utils cache
# -------------------------
def _cached(key: str, ttl: int):
    now = time.time()
    item = _CACHE.get(key)
    if item and (now - item["ts"] < ttl):
        return item["val"]
    return None

def _set_cache(key: str, val):
    _CACHE[key] = {"ts": time.time(), "val": val}

# -------------------------
# HTTP utils
# -------------------------
def _get(url: str, params=None, timeout=6, retries=3) -> Dict[str, Any]:
    """
    GET robuste:
     - retry seulement sur 429/5xx
     - 4xx (400/404) -> pas de retry, on renvoie {}
    """
    for i in range(retries):
        try:
            r = requests.get(url, params=params, timeout=timeout)
            if r.status_code == 200:
                return r.json()
            # 429 or 5xx => retry with backoff
            if r.status_code == 429 or 500 <= r.status_code < 600:
                LOGGER.warning("GET %s -> %s, retry %d/%d", r.url, r.status_code, i+1, retries)
                time.sleep(0.5 * (2 ** i))
                continue
            # 4xx non-retryable
            if 400 <= r.status_code < 500:
                LOGGER.debug("GET %s -> %s (no retry)", r.url, r.status_code)
                return {}
            # Autres cas improbables
            LOGGER.warning("GET %s -> %s", r.url, r.status_code)
            return {}
        except Exception as e:
            # Erreur réseau -> retry
            LOGGER.warning("GET %s failed (%s) retry %d/%d", url, e, i+1, retries)
            time.sleep(0.5 * (2 ** i))
    LOGGER.error("GET %s failed after retries", url)
    return {}

# -------------------------
# Binance symbols (Futures)
# -------------------------
def _load_binance_futures_symbols(force: bool = False) -> Set[str]:
    global _BINANCE_FUTURES_SYMBOLS, _BINANCE_FUTURES_LAST_LOAD
    now = time.time()
    if (not force) and _BINANCE_FUTURES_SYMBOLS and (now - _BINANCE_FUTURES_LAST_LOAD < _TTL_SYMBOLS):
        return _BINANCE_FUTURES_SYMBOLS

    data = _get("https://fapi.binance.com/fapi/v1/exchangeInfo", timeout=10, retries=3)
    symbols = set()
    try:
        for s in (data.get("symbols") or []):
            sym = str(s.get("symbol") or "").upper()
            if sym:
                symbols.add(sym)
    except Exception as e:
        LOGGER.warning("Failed to parse Binance futures exchangeInfo: %s", e)

    if symbols:
        _BINANCE_FUTURES_SYMBOLS = symbols
        _BINANCE_FUTURES_LAST_LOAD = now
        LOGGER.info("Loaded %d Binance futures symbols", len(symbols))
    return _BINANCE_FUTURES_SYMBOLS

def _map_kucoin_to_binance_futures(kucoin_symbol: str) -> Optional[str]:
    """
    Transforme 'XBTUSDTM' -> 'BTCUSDT'
    Règles:
      1) enlever suffixes 'USDTM' / 'USDM'
      2) alias (XBT->BTC)
      3) append 'USDT'
      4) vérifier l'existence via le cache Binance Futures
    Retourne None si introuvable.
    """
    if not kucoin_symbol:
        return None
    s = kucoin_symbol.upper().strip()
    for suf in ("USDTM", "USDM"):
        if s.endswith(suf):
            s = s[: -len(suf)]
            break
    # alias
    base = _ALIAS.get(s, s)
    candidate = base + "USDT"

    symbols = _load_binance_futures_symbols()
    if candidate in symbols:
        return candidate

    # Tentative fallback: certains ont déjà 'USDT' en base (rare)
    if base.endswith("USDT") and base in symbols:
        return base

    # Pas listé chez Binance Futures -> None
    return None

# -------------------------
# Fetchers (Binance Futures)
# -------------------------
def fetch_open_interest(binance_symbol: str) -> float:
    """
    Returns openInterest for a Binance Futures symbol (e.g., 'BTCUSDT') or -1 if unavailable.
    """
    key = f"oi:{binance_symbol}"
    c = _cached(key, _TTL_SHORT)
    if c is not None:
        return c
    if not binance_symbol:
        return -1.0
    data = _get("https://fapi.binance.com/fapi/v1/openInterest", params={"symbol": binance_symbol})
    val = -1.0
    try:
        if data:
            val = float(data.get("openInterest", -1.0))
    except Exception:
        val = -1.0
    _set_cache(key, val)
    return val

def fetch_latest_funding_rate(binance_symbol: str) -> float:
    key = f"fund:{binance_symbol}"
    c = _cached(key, _TTL_SHORT)
    if c is not None:
        return c
    if not binance_symbol:
        return 0.0
    data = _get("https://fapi.binance.com/fapi/v1/premiumIndex", params={"symbol": binance_symbol})
    val = 0.0
    try:
        if data:
            val = float(data.get("lastFundingRate", 0.0))
    except Exception:
        val = 0.0
    _set_cache(key, val)
    return val

def fetch_cvd(binance_symbol: str, limit: int = 120) -> float:
    """
    Approx CVD via aggTrades sur Binance **Futures** (pas Spot).
    Retourne delta buy - sell (proxy via 'm' maker flag).
    """
    key = f"cvd:{binance_symbol}:{limit}"
    c = _cached(key, _TTL_SHORT)
    if c is not None:
        return c
    if not binance_symbol:
        return 0.0
    data = _get("https://fapi.binance.com/fapi/v1/aggTrades", params={"symbol": binance_symbol, "limit": limit})
    buy = sell = 0.0
    try:
        for t in data or []:
            qty = float(t.get("q", 0.0))
            maker = t.get("m", False)
            if maker:
                # buyer is maker -> typically a sell aggressor
                sell += qty
            else:
                buy += qty
    except Exception:
        pass
    delta = buy - sell
    _set_cache(key, delta)
    return delta

# -------------------------
# Scoring
# -------------------------
def _score_oi_change(current_oi: float, prev_oi: float, threshold_pct: float = 0.03) -> int:
    if current_oi <= 0 or prev_oi <= 0:
        return 0
    try:
        pct = (current_oi - prev_oi) / prev_oi
        return 1 if abs(pct) >= threshold_pct else 0
    except Exception:
        return 0

def _score_funding(funding_rate: float, bias: str, threshold: float = 0.0001) -> int:
    if abs(funding_rate) < threshold:
        return 0
    if bias == "LONG":
        return 1 if funding_rate > 0 else 0
    else:
        return 1 if funding_rate < 0 else 0

def _score_cvd(cvd_value: float, bias: str, min_abs: float = 0.0) -> int:
    if abs(cvd_value) < min_abs:
        return 0
    return 1 if ((cvd_value > 0 and bias == "LONG") or (cvd_value < 0 and bias == "SHORT")) else 0

def compute_institutional_score(kucoin_symbol: str, bias: str, prev_oi: float = None) -> Dict[str, Any]:
    """
    Retourne dict complet, ou des valeurs neutres si symbole non mappable sur Binance Futures.
    """
    bias = (bias or "LONG").upper()
    b_symbol = _map_kucoin_to_binance_futures(kucoin_symbol)

    if not b_symbol:
        # Pas de data Binance pour ce symbole -> neutre
        return {
            "symbol": kucoin_symbol,
            "binance_symbol": None,
            "bias": bias,
            "openInterest": -1.0,
            "fundingRate": 0.0,
            "cvd": 0.0,
            "scores": {"oi": 0, "fund": 0, "cvd": 0},
            "score_total": 0,
            "note": "No Binance futures mapping",
        }

    oi = fetch_open_interest(b_symbol)
    fund = fetch_latest_funding_rate(b_symbol)
    cvd = fetch_cvd(b_symbol, limit=120)

    # Scores
    score_oi = 0
    if prev_oi is not None and prev_oi > 0 and oi > 0:
        score_oi = _score_oi_change(oi, prev_oi)
    elif oi > 1e6:
        score_oi = 1

    score_f = _score_funding(fund, bias)
    score_c = _score_cvd(cvd, bias, min_abs=0.0)
    total = int(score_oi + score_f + score_c)

    return {
        "symbol": kucoin_symbol,
        "binance_symbol": b_symbol,
        "bias": bias,
        "openInterest": oi,
        "fundingRate": fund,
        "cvd": cvd,
        "scores": {"oi": score_oi, "fund": score_f, "cvd": score_c},
        "score_total": total,
    }
