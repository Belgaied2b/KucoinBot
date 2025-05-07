import pandas as pd
import requests

def fetch_klines(symbol, interval="1h", limit=150):
    granularity = {"1h": 60, "4h": 240}[interval]
    url = "https://api-futures.kucoin.com/api/v1/kline/query"
    params = {"symbol": symbol, "granularity": granularity, "limit": limit}
    response = requests.get(url, params=params)
    data = response.json()["data"]

    # KuCoin retourne 6 colonnes : [timestamp, open, close, high, low, volume]
    df = pd.DataFrame(data, columns=["timestamp", "open", "close", "high", "low", "volume"])
    df = df.astype(float)
    return df[["open", "high", "low", "close", "volume"]]
