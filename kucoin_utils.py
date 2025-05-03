from kucoin.client import Market
import pandas as pd
import time

client = Market(url='https://api-futures.kucoin.com')

def get_kucoin_perps():
    markets = client._request('GET', '/api/v1/contracts/active')
    return [m['symbol'] for m in markets if m['quoteCurrency'] == 'USDT']

def is_valid_granularity(symbol, granularity=14400):
    try:
        client._request('GET', '/api/v1/kline/query', params={
            'symbol': symbol,
            'granularity': granularity,
            'limit': 1
        })
        return True
    except:
        return False

def fetch_klines(symbol, interval="4hour", limit=150):
    seconds = {
        "4hour": 14400
    }[interval]

    raw = client._request('GET', '/api/v1/kline/query', params={
        'symbol': symbol,
        'granularity': seconds,
        'limit': limit
    })
    df = pd.DataFrame(raw['data'], columns=["timestamp", "open", "high", "low", "close", "volume", "turnover"])
    df["timestamp"] = pd.to_datetime(df["timestamp"], unit="s")
    df.set_index("timestamp", inplace=True)
    df = df.astype(float)
    time.sleep(0.2)
    return df
