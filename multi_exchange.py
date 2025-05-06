# multi_exchange.py
from collections import defaultdict
import pandas as pd

class ExchangeAggregator:
    """
    Agrège les OHLCV reçues en WS sur plusieurs exchanges.
    Ici on en fait la moyenne simple sur la dernière bougie.
    """
    def __init__(self, data_stream):
        self.ds = data_stream

    def get_ohlcv_df(self, symbol: str) -> pd.DataFrame:
        """
        Construit un DataFrame OHLCV unifié à partir des dernières données WS.
        (Simplification : on prend la plus récente de chaque exchange.)
        """
        rows = []
        for (ex, sym), o in self.ds.ohlcv.items():
            if sym == symbol:
                rows.append(o)
        if not rows:
            return None
        # transformer en DF pandas
        df = pd.DataFrame(rows, columns=['timestamp','open','high','low','close','volume'])
        df['timestamp'] = pd.to_datetime(df['timestamp'], unit='ms')
        df = df.set_index('timestamp').sort_index()
        return df

    def get_orderbook(self, symbol: str):
        """
        Renvoie la liste des orderbooks reçus pour symbol ;
        on pourra les fusionner en fonction du besoin.
        """
        obs = []
        for (ex, sym), ob in self.ds.orderbooks.items():
            if sym == symbol:
                obs.append(ob)
        return obs
