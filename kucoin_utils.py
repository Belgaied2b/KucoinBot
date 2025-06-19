import requests
import pandas as pd

BASE_URL = "https://api.kucoin.com"

def get_kucoin_symbols():
    try:
        response = requests.get(f"{BASE_URL}/api/v1/contracts/active")
        data = response.json()["data"]
        symbols = [item["symbol"] for item in data if item["symbol"].endswith("USDTM")]
        return symbols
    except Exception as e:
        print(f"Erreur lors de la récupération des symboles KuCoin : {e}")
        return []

def fetch_klines(symbol, interval="1h", limit=200):
    try:
        url = f"{BASE_URL}/api/v1/market/candles?symbol={symbol}&granularity={convert_interval(interval)}"
        response = requests.get(url)
        response.raise_for_status()
        raw_data = response.json()["data"][:limit]
        df = pd.DataFrame(raw_data, columns=["timestamp", "open", "high", "low", "close", "volume", "turnover"])
        df = df.iloc[::-1]  # inverse l’ordre
        df["timestamp"] = pd.to_datetime(df["timestamp"], unit="s")
        df[["open", "high", "low", "close", "volume"]] = df[["open", "high", "low", "close", "volume"]].astype(float)
        return df
    except Exception as e:
        print(f"Erreur fetch_klines pour {symbol}: {e}")
        return None

def convert_interval(interval):
    mapping = {
        "1m": 60,
        "3m": 180,
        "5m": 300,
        "15m": 900,
        "30m": 1800,
        "1h": 3600,
        "4h": 14400,
        "1d": 86400
    }
    return mapping.get(interval, 3600)
