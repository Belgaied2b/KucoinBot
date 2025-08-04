import requests
import pandas as pd
import time

BASE_URL = "https://api-futures.kucoin.com"

# Récupère la liste des contrats PERP (USDT-M)
def get_perp_symbols():
    url = f"{BASE_URL}/api/v1/contracts/active"
    response = requests.get(url)
    data = response.json()
    symbols = [
        x["symbol"]
        for x in data["data"]
        if x["symbol"].endswith("USDTM") and x["enableTrading"]
    ]
    return symbols

# Récupère les chandeliers d'une paire
def get_klines(symbol, interval="1hour", limit=200):
    url = f"{BASE_URL}/api/v1/kline/query"
    end_time = int(time.time() * 1000)
    params = {
        "symbol": symbol,
        "granularity": convert_interval(interval),
        "from": (end_time // 1000) - limit * interval_to_seconds(interval),
        "to": end_time // 1000,
    }
    try:
        response = requests.get(url, params=params)
        response.raise_for_status()
        data = response.json()["data"]
        if not data:
            return pd.DataFrame()
        df = pd.DataFrame(data, columns=[
            "timestamp", "open", "high", "low", "close", "volume", "turnover"
        ])
        df["timestamp"] = pd.to_datetime(df["timestamp"], unit="ms")
        df.set_index("timestamp", inplace=True)
        df = df.astype(float)
        df.name = symbol
        return df
    except Exception as e:
        print(f"[ERREUR] get_klines {symbol} → {e}")
        return pd.DataFrame()

# Utilitaires
def convert_interval(interval):
    if interval.endswith("min"):
        return int(interval.replace("min", "")) * 60
    if interval.endswith("hour"):
        return int(interval.replace("hour", "")) * 3600
    if interval == "1day":
        return 86400
    return 3600  # défaut 1H

def interval_to_seconds(interval):
    if interval.endswith("min"):
        return int(interval.replace("min", "")) * 60
    if interval.endswith("hour"):
        return int(interval.replace("hour", "")) * 3600
    if interval == "1day":
        return 86400
    return 3600
