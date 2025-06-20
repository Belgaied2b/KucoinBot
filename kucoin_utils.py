import pandas as pd
import requests

def fetch_all_symbols():
    """
    Récupère tous les contrats PERP actifs en USDTM depuis KuCoin.
    """
    url = "https://api-futures.kucoin.com/api/v1/contracts/active"
    response = requests.get(url)
    data = response.json().get("data", [])
    return [item["symbol"] for item in data if item["symbol"].endswith("USDTM")]

def fetch_klines(symbol, interval="1h", limit=150):
    """
    Récupère les chandeliers historiques (klines) pour un symbole donné.
    Supporte 1h et 4h via 'granularity'.
    """
    granularity_map = {"1h": 60, "4h": 240}
    granularity = granularity_map.get(interval, 60)

    url = "https://api-futures.kucoin.com/api/v1/kline/query"
    params = {"symbol": symbol, "granularity": granularity, "limit": limit}
    response = requests.get(url, params=params)
    data = response.json().get("data", [])

    if not data:
        return None

    df = pd.DataFrame(data, columns=["timestamp", "open", "close", "high", "low", "volume"])
    df = df.astype(float)

    # 🔎 Détection intelligente de l’unité de timestamp
    sample_ts = df["timestamp"].iloc[0]
    if sample_ts > 1e12:
        unit = "ms"
    elif sample_ts > 1e10:
        unit = "s"
    else:
        unit = "s"

    df["timestamp"] = pd.to_datetime(df["timestamp"].astype("int64"), unit=unit)
    df = df[["timestamp", "open", "high", "low", "close", "volume"]]
    df.set_index("timestamp", inplace=True)

    return df
