"""
Collecte en temps réel des métriques institutionnelles (Open Interest, Funding, CVD/Delta)
et génération d’un score institutionnel global, avec cache anti-429.
"""
import time, logging, requests
_CACHE = {}; _TTL = 10
LOGGER = logging.getLogger(__name__)

def _cached(k, ttl=_TTL):
    v = _CACHE.get(k); now = time.time()
    return v["val"] if v and now - v["ts"] < ttl else None

def _set(k, v): _CACHE[k] = {"ts": time.time(), "val": v}

def _get(url, params=None, retries=3, timeout=6):
    for i in range(retries):
        try:
            r = requests.get(url, params=params, timeout=timeout)
            r.raise_for_status()
            return r.json()
        except Exception as e:
            LOGGER.warning("GET %s failed (%s) try %d/%d", url, e, i+1, retries)
            time.sleep(0.4 * (2**i))
    return {}

def fetch_open_interest(symbol: str) -> float:
    key=f"oi:{symbol}"; c=_cached(key,12)
    if c is not None: return c
    d=_get("https://fapi.binance.com/fapi/v1/openInterest", {"symbol": symbol})
    val=float(d.get("openInterest", -1)) if d else -1.0; _set(key,val); return val

def fetch_latest_funding_rate(symbol: str) -> float:
    key=f"fund:{symbol}"; c=_cached(key,12)
    if c is not None: return c
    d=_get("https://fapi.binance.com/fapi/v1/premiumIndex", {"symbol": symbol})
    val=float(d.get("lastFundingRate", 0.0)) if d else 0.0; _set(key,val); return val

def fetch_cvd(symbol: str, limit=120) -> float:
    key=f"cvd:{symbol}:{limit}"; c=_cached(key,6)
    if c is not None: return c
    d=_get("https://api.binance.com/api/v3/aggTrades", {"symbol": symbol, "limit": limit})
    buy=sell=0.0
    for t in d or []:
        q=float(t.get("q",0))
        if t.get("m"): sell+=q
        else: buy+=q
    delta=buy-sell; _set(key,delta); return delta

def compute_institutional_score(symbol: str, bias: str, prev_oi: float = None):
    s = symbol.upper().replace("/", "")
    oi = fetch_open_interest(s)
    fund = fetch_latest_funding_rate(s)
    cvd = fetch_cvd(s)
    score_oi = 1 if (prev_oi and prev_oi>0 and oi>0 and abs(oi-prev_oi)/prev_oi>0.03) else (1 if oi>1e6 else 0)
    score_f = 1 if ((fund>0 and bias=="LONG") or (fund<0 and bias=="SHORT")) else 0
    score_c = 1 if ((cvd>0 and bias=="LONG") or (cvd<0 and bias=="SHORT")) else 0
    total = score_oi + score_f + score_c
    return {
        "symbol": s, "bias": bias,
        "openInterest": oi, "fundingRate": fund, "cvd": cvd,
        "scores": {"oi": score_oi, "fund": score_f, "cvd": score_c},
        "score_total": total,
    }
