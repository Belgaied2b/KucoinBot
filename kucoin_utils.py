from kucoin_futures.client import Market
import pandas as pd
import time

market = Market()

def get_klines(symbol: str, interval: str = '1hour', limit: int = 150):
    try:
        data = market.get_kline_data(symbol=symbol, kline_type=interval, limit=limit)
        if not data:
            return None

        df = pd.DataFrame(data, columns=[
            'timestamp', 'open', 'high', 'low', 'close', 'volume', 'turnover'
        ])
        df['timestamp'] = pd.to_datetime(df['timestamp'], unit='ms')
        df = df.astype({
            'open': 'float',
            'high': 'float',
            'low': 'float',
            'close': 'float',
            'volume': 'float'
        })
        return df
    except Exception as e:
        print(f"Erreur chargement bougies pour {symbol} : {e}")
        return None

def get_symbols_data():
    try:
        symbols = market.get_contracts_list()
        usdtm_symbols = [s['symbol'] for s in symbols if s['quoteCurrency'] == 'USDT' and s['enableTrading']]

        data = {}
        for symbol in usdtm_symbols:
            df_1h = get_klines(symbol, interval='1hour')
            df_4h = get_klines(symbol, interval='4hour')

            if df_1h is not None and df_4h is not None and len(df_1h) > 50 and len(df_4h) > 50:
                data[symbol] = {
                    "1h": df_1h,
                    "4h": df_4h
                }
            time.sleep(0.3)  # anti rate-limit
        return data
    except Exception as e:
        print(f"Erreur récupération symboles : {e}")
        return {}
