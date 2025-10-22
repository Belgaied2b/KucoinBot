import requests, pandas as pd, logging
LOGGER = logging.getLogger(__name__)

def fetch_all_symbols():
    url = "https://api-futures.kucoin.com/api/v1/contracts/active"
    r = requests.get(url, timeout=10); r.raise_for_status()
    data = r.json().get("data", [])
    return [s["symbol"] for s in data if s.get("quoteCurrency") == "USDT" and s.get("enableTrading")]

def _granularity(interval: str) -> int:
    return {"1m":60, "5m":300, "15m":900, "1h":3600, "4h":14400, "1d":86400}.get(interval, 3600)

def fetch_klines(symbol: str, interval="1h", limit=200) -> pd.DataFrame:
    url = "https://api-futures.kucoin.com/api/v1/kline/query"
    params = {"symbol": symbol, "granularity": _granularity(interval), "limit": limit}
    r = requests.get(url, params=params, timeout=12); r.raise_for_status()
    raw = r.json().get("data", [])
    if not raw: return pd.DataFrame(columns=["time","open","high","low","close","volume"])
    df = pd.DataFrame(raw, columns=["time","open","high","low","close","volume"])
    for c in ["open","high","low","close","volume"]: df[c] = df[c].astype(float)
    return df
