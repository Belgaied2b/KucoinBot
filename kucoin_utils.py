from kucoin.client import Market
import pandas as pd

client = Market(url='https://api.kucoin.com')

def get_kucoin_perps():
    data = client.get_contract_symbols()
    return [d['symbol'] for d in data if d['symbol'].endswith('USDTM')]

def fetch_klines(symbol):
    raw = client.get_kline_data(symbol, '4hour', 200)
    df = pd.DataFrame(raw, columns=[
        "timestamp", "open", "close", "high", "low", "volume", "turnover"
    ])
    df = df.astype(float)
    df['timestamp'] = pd.to_datetime(df['timestamp'], unit='s')
    df.set_index('timestamp', inplace=True)
    return df
