import requests
import pandas as pd
import time

BASE_URL = "https://api-futures.kucoin.com"

# ✅ Récupère tous les symboles PERP actifs (USDTM)
def get_all_symbols():
    try:
        url = f"{BASE_URL}/api/v1/contracts/active"
        response = requests.get(url)
        response.raise_for_status()
        data = response.json().get("data", [])
        symbols = [
            item["symbol"]
            for item in data
            if item.get("symbol", "").endswith("USDTM") and item.get("enableTrading", False)
        ]
        if not symbols:
            print("[WARN] Aucun symbole PERP USDTM actif trouvé.")
        return symbols
    except Exception as e:
        print(f"[ERREUR] get_all_symbols → {e}")
        return []

# Alias de compatibilité
get_perp_symbols = get_all_symbols

# ✅ Récupère les chandeliers pour un symbole donné
def get_klines(symbol, interval="1hour", limit=200):
    try:
        end_time = int(time.time())
        params = {
            "symbol": symbol,
            "granularity": convert_interval(interval),
            "from": end_time - limit * interval_to_seconds(interval),
            "to": end_time,
        }
        url = f"{BASE_URL}/api/v1/kline/query"
        response = requests.get(url, params=params)
        response.raise_for_status()
        data = response.json().get("data", [])
        if not data:
            print(f"[WARN] Pas de données pour {symbol}")
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

# 🔧 Convertit un intervalle texte (ex: "1hour") en secondes
def convert_interval(interval):
    if interval.endswith("min"):
        return int(interval.replace("min", "")) * 60
    if interval.endswith("hour"):
        return int(interval.replace("hour", "")) * 3600
    if interval == "1day":
        return 86400
    return 3600  # par défaut 1h

# 🔧 Convertisseur simple pour usage dans les calculs de timestamp
def interval_to_seconds(interval):
    return convert_interval(interval)
