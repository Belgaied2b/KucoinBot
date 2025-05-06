# data_stream.py

import asyncio
import ccxt.pro as ccxtpro
from collections import defaultdict

class DataStream:
    """
    Gère les flux WebSocket OHLCV et orderbook pour un ou plusieurs exchanges.
    """
    def __init__(self, exchanges: list, symbols: list, timeframe: str):
        # Instancie les clients ccxt.pro
        self.exchanges = {
            name: getattr(ccxtpro, name)() 
            for name in exchanges
        }
        self.symbols   = symbols
        self.timeframe = timeframe
        # Stocke la dernière bougie et le dernier orderbook pour chaque (exchange, symbol)
        self.ohlcv     = defaultdict(dict)
        self.orderbooks= defaultdict(dict)

    async def start(self, on_update):
        # 1) Charge les marchés CCXT pour chaque exchange
        for name, ex in self.exchanges.items():
            await ex.load_markets()
        # 2) Lance les watchers pour OHLCV et orderbook
        tasks = []
        for name, ex in self.exchanges.items():
            for symbol in self.symbols:
                if symbol not in ex.symbols:
                    print(f"[{name}][{symbol}] warning: symbole non supporté")
                    continue
                tasks.append(self._ws_ohlcv(name, ex, symbol, on_update))
                tasks.append(self._ws_orderbook(name, ex, symbol, on_update))
        await asyncio.gather(*tasks)

    async def _ws_ohlcv(self, name, ex, symbol, on_update):
        """
        Récupère un flux de bougies, ne garde que la dernière pour l'analyse.
        """
        while True:
            try:
                # Renvoie une liste de bougies, chacune [ts, open, high, low, close, vol]
                candles = await ex.watch_ohlcv(symbol, self.timeframe)
                latest  = candles[-1]
                self.ohlcv[(name, symbol)] = latest
                await on_update('ohlcv', name, symbol, latest)
            except Exception as e:
                print(f"[{name}][{symbol}][OHLCV] error:", e)
                await asyncio.sleep(5)

    async def _ws_orderbook(self, name, ex, symbol, on_update):
        """
        Récupère en continu le carnet d'ordres.
        """
        while True:
            try:
                ob = await ex.watch_order_book(symbol)
                self.orderbooks[(name, symbol)] = ob
                await on_update('orderbook', name, symbol, ob)
            except Exception as e:
                print(f"[{name}][{symbol}][OB] error:", e)
                await asyncio.sleep(5)
