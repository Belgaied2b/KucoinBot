from kucoin_futures.client import Market
import pandas as pd
import time

client = Market()

def get_kucoin_perps():
    markets = client.get_symbols()
    return [m['symbol'] for m in markets if m['quoteCurrency'] == 'USDT']

def fetch_klines(symbol, interval="4hour", limit=150):
    raw = client.get_kline(symbol, interval=interval, startAt=None, endAt=None)
    df = pd.DataFrame(raw, columns=["timestamp", "open", "high", "low", "close", "volume", "turnover"])
    df["timestamp"] = pd.to_datetime(df["timestamp"], unit="s")  # Attention : secondes pour l’API Futures
    df.set_index("timestamp", inplace=True)
    df = df.astype(float)
    time.sleep(0.2)
    return df.tail(limit)
