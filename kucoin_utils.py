import pandas as pd
import requests

def fetch_symbols():
    url = "https://api-futures.kucoin.com/api/v1/contracts/active"
    response = requests.get(url)
    data = response.json()["data"]
    return [item["symbol"] for item in data if item["symbol"].endswith("USDTM")]

def fetch_klines(symbol, interval="1h", limit=150):
    granularity = {"1h": 60, "4h": 240}[interval]
    url = "https://api-futures.kucoin.com/api/v1/kline/query"
    params = {"symbol": symbol, "granularity": granularity, "limit": limit}
    response = requests.get(url, params=params)
    data = response.json()["data"]

    # Ajoute toutes les colonnes y compris timestamp
    df = pd.DataFrame(data, columns=["timestamp", "open", "close", "high", "low", "volume"])
    df = df.astype(float)

    # Convertit timestamp en datetime (facultatif mais utile pour les graphes)
    df["timestamp"] = pd.to_datetime(df["timestamp"], unit="s")

    return df[["timestamp", "open", "high", "low", "close", "volume"]]
