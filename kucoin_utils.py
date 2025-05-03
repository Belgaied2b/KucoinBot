from kucoin.client import Market
import pandas as pd
import time

# Connexion à l'API KuCoin Futures
client = Market(url='https://api-futures.kucoin.com')

def get_kucoin_perps():
    markets = client.get_symbols()
    return [m['symbol'] for m in markets if m['quoteCurrency'] == 'USDT']

def fetch_klines(symbol, interval="4hour", limit=150):
    raw = client.get_kline(symbol, interval, limit=limit)
    df = pd.DataFrame(raw, columns=["timestamp", "open", "high", "low", "close", "volume", "turnover"])
    df["timestamp"] = pd.to_datetime(df["timestamp"], unit="ms")
    df.set_index("timestamp", inplace=True)
    df = df.astype(float)
    time.sleep(0.2)
    return df
