### kucoin_utils.py
```python
import pandas as pd
import requests

def fetch_klines(symbol, interval="1h", limit=150):
    granularity = {"1h": 60, "4h": 240}[interval]
    url = f"https://api-futures.kucoin.com/api/v1/kline/query"
    params = {"symbol": symbol, "granularity": granularity, "limit": limit}
    response = requests.get(url, params=params)
    data = response.json()["data"]
    df = pd.DataFrame(data, columns=["timestamp", "open", "high", "low", "close", "volume", "_", "_"])
    df = df.astype(float)
    return df[["open", "high", "low", "close", "volume"]]
```
