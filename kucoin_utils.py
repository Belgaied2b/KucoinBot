import requests
import pandas as pd

BASE_URL = "https://api.kucoin.com"

def get_all_perp_symbols():
    url = f"{BASE_URL}/api/v1/contracts/active"
    response = requests.get(url)
    data = response.json()["data"]
    symbols = [item["symbol"] for item in data if item["symbol"].endswith("USDTM")]
    return symbols

def get_klines(symbol, interval="1h", limit=200):
    url = f"{BASE_URL}/api/v1/market/candles?type={interval}&symbol={symbol}&limit={limit}"
    try:
        response = requests.get(url)
        data = response.json().get("data", [])
        if not data:
            return None
        df = pd.DataFrame(data, columns=[
            "timestamp", "open", "high", "low", "close", "volume", "turnover"
        ])
        df = df.iloc[::-1]  # Chrono
        df[["open", "high", "low", "close", "volume"]] = df[["open", "high", "low", "close", "volume"]].astype(float)
        df["timestamp"] = pd.to_datetime(df["timestamp"], unit="s")
        df.set_index("timestamp", inplace=False)
        return df
    except Exception as e:
        print(f"Erreur lors du chargement des données pour {symbol} : {e}")
        return None
