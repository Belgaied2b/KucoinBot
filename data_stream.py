# data_stream.py
import asyncio
import ccxt.pro as ccxtpro
from collections import defaultdict

class DataStream:
    """
    Souscrit en WS aux OHLCV et orderbooks de plusieurs exchanges.
    Stocke les dernières données dans self.ohlcv et self.orderbooks.
    """
    def __init__(self, exchanges: list, symbols: list, timeframe: str):
        self.exchanges = {name: getattr(ccxtpro, name)() for name in exchanges}
        self.symbols = symbols
        self.timeframe = timeframe
        self.ohlcv = defaultdict(dict)
        self.orderbooks = defaultdict(dict)

    async def start(self, on_update):
        tasks = []
        for name, ex in self.exchanges.items():
            for symbol in self.symbols:
                tasks.append(self._ws_ohlcv(name, ex, symbol, on_update))
                tasks.append(self._ws_orderbook(name, ex, symbol, on_update))
        await asyncio.gather(*tasks)

    async def _ws_ohlcv(self, name, ex, symbol, on_update):
        while True:
            try:
                data = await ex.watch_ohlcv(symbol, self.timeframe)
                # data: [timestamp, open, high, low, close, volume]
                self.ohlcv[(name, symbol)] = data
                await on_update('ohlcv', name, symbol, data)
            except Exception as e:
                print(f"[{name}][{symbol}][OHLCV] error:", e)
                await asyncio.sleep(5)

    async def _ws_orderbook(self, name, ex, symbol, on_update):
        while True:
            try:
                ob = await ex.watch_order_book(symbol)
                # ob: {'bids': [[price, size],...], 'asks': [...]}
                self.orderbooks[(name, symbol)] = ob
                await on_update('orderbook', name, symbol, ob)
            except Exception as e:
                print(f"[{name}][{symbol}][OB] error:", e)
                await asyncio.sleep(5)
