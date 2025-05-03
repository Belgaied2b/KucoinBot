import httpx
import pandas as pd
import time

BASE_URL = "https://api.kucoin.com"

def get_kucoin_perps():
    url = f"{BASE_URL}/api/v1/contracts/active"
    response = httpx.get(url)
    response.raise_for_status()
    data = response.json()
    return [d["symbol"] for d in data["data"] if d["symbol"].endswith("USDTM")]

def fetch_klines(symbol, interval="4hour", limit=100):
    url = f"{BASE_URL}/api/v1/market/candles"
    params = {
        "symbol": symbol,
        "type": interval,
        "limit": limit
    }
    try:
        response = httpx.get(url, params=params)
        response.raise_for_status()
        data = response.json()
        if "data" not in data:
            return None
        df = pd.DataFrame(data["data"], columns=[
            "time", "open", "close", "high", "low", "volume", "turnover"
        ])
        df = df.iloc[::-1].copy()
        df["time"] = pd.to_datetime(df["time"], unit='ms')
        df[["open", "close", "high", "low", "volume"]] = df[["open", "close", "high", "low", "volume"]].astype(float)
        return df
    except Exception as e:
        print(f"Erreur fetch_klines pour {symbol}: {e}")
        return None
