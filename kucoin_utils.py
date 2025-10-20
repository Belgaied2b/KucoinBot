"""
kucoin_utils.py
Utilitaires KuCoin Futures pour récupérer symboles et chandeliers.
"""
import requests

def fetch_all_symbols():
    url = "https://api-futures.kucoin.com/api/v1/contracts/active"
    data = requests.get(url).json()
    return [s["symbol"] for s in data["data"] if s["quoteCurrency"] == "USDT"]

def fetch_klines(symbol: str, interval="1h", limit=100):
    url = f"https://api-futures.kucoin.com/api/v1/kline/query"
    params = {"symbol": symbol, "granularity": 3600, "limit": limit}
    data = requests.get(url, params=params).json()
    import pandas as pd
    df = pd.DataFrame(data["data"], columns=["time","open","high","low","close","volume"])
    df = df.astype(float)
    return df
