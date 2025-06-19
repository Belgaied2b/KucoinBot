import pandas as pd
import requests

def get_kucoin_symbols():
    url = "https://api-futures.kucoin.com/api/v1/contracts/active"
    try:
        response = requests.get(url)
        response.raise_for_status()
        data = response.json()["data"]
        return [item["symbol"] for item in data if item["symbol"].endswith("USDTM")]
    except Exception as e:
        print(f"Erreur récupération des symboles KuCoin : {e}")
        return []

def fetch_klines(symbol, interval="1h", limit=150):
    granularity_map = {"1h": 60, "4h": 240}
    granularity = granularity_map.get(interval, 60)
    
    url = "https://api-futures.kucoin.com/api/v1/kline/query"
    params = {"symbol": symbol, "granularity": granularity, "limit": limit}
    
    try:
        response = requests.get(url, params=params)
        response.raise_for_status()
        data = response.json()["data"]

        if not data:
            return pd.DataFrame()

        df = pd.DataFrame(data, columns=["timestamp", "open", "close", "high", "low", "volume"])
        df = df.astype(float)

        # ✅ Détection robuste de l’unité du timestamp
        sample_ts = df["timestamp"].iloc[0]
        if sample_ts > 1e12:
            unit = "ms"
        elif sample_ts > 1e10:
            unit = "s"
        else:
            unit = "s"

        df["timestamp"] = pd.to_datetime(df["timestamp"].astype("int64"), unit=unit)
        return df[["timestamp", "open", "high", "low", "close", "volume"]]
    
    except Exception as e:
        print(f"Erreur récupération des données {symbol} : {e}")
        return pd.DataFrame()
