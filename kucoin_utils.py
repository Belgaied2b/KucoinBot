# kucoin_utils.py

from kucoin.client import Market
import pandas as pd
import time

client = Market()

def get_kucoin_perps():
    contracts = client.get_contracts_list()
    return [c["symbol"] for c in contracts if c["symbol"].endswith("USDTM")]

def fetch_klines(symbol):
    try:
        klines = client.get_kline(symbol=symbol, kline_type="4hour", limit=100)
        df = pd.DataFrame(klines, columns=[
            "timestamp", "open", "high", "low", "close", "volume", "turnover"
        ])
        df["timestamp"] = pd.to_datetime(df["timestamp"], unit="ms")
        df.set_index("timestamp", inplace=True)
        df = df.astype(float)
        return df
    except Exception as e:
        print(f"Erreur récupération données pour {symbol}: {e}")
        return None
