"""
institutional_live.py
Collecte en temps réel des métriques institutionnelles (Open Interest, Funding, CVD/Delta)
et génération d’un score institutionnel global.
"""
import time
import logging
import requests

_CACHE = {}
_CACHE_TTL = 10  # secondes
LOGGER = logging.getLogger(__name__)

def _cached(key: str, ttl: int = _CACHE_TTL):
    now = time.time()
    if key in _CACHE and now - _CACHE[key]["ts"] < ttl:
        return _CACHE[key]["val"]
    return None

def _set_cache(key, val):
    _CACHE[key] = {"ts": time.time(), "val": val}

def _safe_get(url, params=None, retries=3, timeout=6):
    for i in range(retries):
        try:
            r = requests.get(url, params=params, timeout=timeout)
            r.raise_for_status()
            return r.json()
        except Exception as e:
            LOGGER.warning(f"GET {url} failed ({e}), retry {i+1}/{retries}")
            time.sleep(0.5 * (2 ** i))
    return {}

def fetch_open_interest(symbol: str) -> float:
    key = f"oi:{symbol}"
    cached = _cached(key, 12)
    if cached is not None:
        return cached
    data = _safe_get("https://fapi.binance.com/fapi/v1/openInterest", {"symbol": symbol})
    val = float(data.get("openInterest", -1)) if data else -1.0
    _set_cache(key, val)
    return val

def fetch_latest_funding_rate(symbol: str) -> float:
    key = f"fund:{symbol}"
    cached = _cached(key, 12)
    if cached is not None:
        return cached
    data = _safe_get("https://fapi.binance.com/fapi/v1/premiumIndex", {"symbol": symbol})
    val = float(data.get("lastFundingRate", 0.0)) if data else 0.0
    _set_cache(key, val)
    return val

def fetch_cvd(symbol: str, limit=100) -> float:
    key = f"cvd:{symbol}"
    cached = _cached(key, 6)
    if cached is not None:
        return cached
    data = _safe_get("https://api.binance.com/api/v3/aggTrades", {"symbol": symbol, "limit": limit})
    buy_vol = sell_vol = 0.0
    for t in data or []:
        qty = float(t.get("q", 0))
        if t.get("m"):
            sell_vol += qty
        else:
            buy_vol += qty
    delta = buy_vol - sell_vol
    _set_cache(key, delta)
    return delta

def compute_institutional_score(symbol: str, bias: str, prev_oi: float = None):
    s = symbol.upper().replace("/", "")
    oi = fetch_open_interest(s)
    fund = fetch_latest_funding_rate(s)
    cvd = fetch_cvd(s)
    score_oi = 1 if prev_oi and abs(oi - prev_oi) / max(prev_oi, 1) > 0.03 else (1 if oi > 1e6 else 0)
    score_f = 1 if ((fund > 0 and bias == "LONG") or (fund < 0 and bias == "SHORT")) else 0
    score_c = 1 if ((cvd > 0 and bias == "LONG") or (cvd < 0 and bias == "SHORT")) else 0
    score_total = score_oi + score_f + score_c
    return {
        "symbol": s,
        "bias": bias,
        "openInterest": oi,
        "fundingRate": fund,
        "cvd": cvd,
        "scores": {"oi": score_oi, "fund": score_f, "cvd": score_c},
        "score_total": score_total,
    }
