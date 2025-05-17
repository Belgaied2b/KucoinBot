import ccxt
import pandas as pd

exchange = ccxt.kucoin()

def fetch_symbols():
    markets = exchange.load_markets()
    symbols = [s for s in markets if "USDT:USDT" in s and "PERP" in s]
    return symbols

def fetch_klines(symbol, interval='1h', limit=200):
    try:
        ohlcv = exchange.fetch_ohlcv(symbol, timeframe=interval, limit=limit)
        df = pd.DataFrame(ohlcv, columns=['timestamp', 'open', 'high', 'low', 'close', 'volume'])
        return df
    except Exception as e:
        print(f"[{symbol}] ⚠️ Erreur fetch_klines : {e}")
        return None
