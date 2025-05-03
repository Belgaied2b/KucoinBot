# kucoin_utils.py

from kucoin_futures.client import Market
import pandas as pd
import time

client = Market()

def get_kucoin_perps():
    try:
        data = client.get_contracts_list()
        return [x["symbol"] for x in data if x["isInverse"] is False]
    except Exception as e:
        print(f"❌ Erreur lors de la récupération des PERP : {e}")
        return []

def fetch_klines(symbol, interval="4h", limit=100):
    try:
        data = client.get_kline_data(symbol, interval, limit)
        df = pd.DataFrame(data, columns=[
            "time", "open", "high", "low", "close", "volume", "turnover"])
        df["time"] = pd.to_datetime(df["time"], unit="ms")
        df = df.sort_values("time")
        df[["open", "high", "low", "close", "volume"]] = df[[
            "open", "high", "low", "close", "volume"]].astype(float)
        return df
    except Exception as e:
        print(f"❌ Erreur pour {symbol} : {e}")
        return None
