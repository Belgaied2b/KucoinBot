import pandas as pd
import requests

def fetch_all_symbols():
    """
    Récupère tous les contrats PERP actifs en USDTM depuis KuCoin.
    """
    url = "https://api-futures.kucoin.com/api/v1/contracts/active"
    try:
        response = requests.get(url, timeout=10)
        response.raise_for_status()
        data = response.json().get("data", [])
        return [item["symbol"] for item in data if item["symbol"].endswith("USDTM")]
    except Exception as e:
        print(f"⚠️ Erreur fetch_all_symbols : {e}")
        return []

def fetch_klines(symbol, interval="1h", limit=150):
    """
    Récupère les chandeliers historiques pour un symbole donné.
    Renvoie un DataFrame avec 'timestamp' et colonnes OHLCV.
    """
    granularity_map = {"1h": 60, "4h": 240}
    granularity = granularity_map.get(interval, 60)

    url = "https://api-futures.kucoin.com/api/v1/kline/query"
    params = {"symbol": symbol, "granularity": granularity, "limit": limit}

    try:
        response = requests.get(url, params=params, timeout=10)
        response.raise_for_status()
        data = response.json().get("data", [])

        if not data or len(data) < 50:
            print(f"[{symbol}] ❌ Données insuffisantes ({len(data)} bougies)")
            return None

        df = pd.DataFrame(data, columns=["timestamp", "open", "close", "high", "low", "volume"])
        df = df.astype(float)

        # Correction de l'unité de temps
        sample_ts = df["timestamp"].iloc[0]
        unit = "ms" if sample_ts > 1e12 else "s"
        df["timestamp"] = pd.to_datetime(df["timestamp"].astype("int64"), unit=unit)

        # Réorganisation des colonnes
        df = df[["timestamp", "open", "high", "low", "close", "volume"]]
        df.set_index("timestamp", inplace=False)

        if df.isnull().values.any():
            print(f"[{symbol}] ⚠️ Données corrompues ou NaN détectées")
            return None

        return df

    except Exception as e:
        print(f"[{symbol}] ⚠️ Erreur fetch_klines : {e}")
        return None
