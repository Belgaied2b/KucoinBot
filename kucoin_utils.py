import pandas as pd
import requests

def fetch_all_symbols():
    """
    Récupère tous les contrats PERP actifs en USDTM depuis KuCoin.
    """
    url = "https://api-futures.kucoin.com/api/v1/contracts/active"
    try:
        response = requests.get(url)
        response.raise_for_status()
        data = response.json().get("data", [])
        return [item["symbol"] for item in data if item["symbol"].endswith("USDTM")]
    except Exception as e:
        print(f"⚠️ Erreur fetch_all_symbols : {e}")
        return []

def fetch_klines(symbol, interval="1h", limit=150):
    """
    Récupère les chandeliers historiques (klines) pour un symbole donné.
    Supporte 1h et 4h via 'granularity'. Logique de vérification renforcée.
    """
    granularity_map = {"1h": 60, "4h": 240}
    granularity = granularity_map.get(interval, 60)

    url = "https://api-futures.kucoin.com/api/v1/kline/query"
    params = {"symbol": symbol, "granularity": granularity, "limit": limit}

    try:
        response = requests.get(url, params=params)
        response.raise_for_status()
        data = response.json().get("data", [])

        if not data or len(data) < 50:
            print(f"[{symbol}] ❌ Données insuffisantes ({len(data)} bougies)")
            return None

        df = pd.DataFrame(data, columns=["timestamp", "open", "close", "high", "low", "volume"])
        df = df.astype(float)

        sample_ts = df["timestamp"].iloc[0]
        unit = "ms" if
