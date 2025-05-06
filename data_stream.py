# data_stream.py

import asyncio
import ccxt.pro as ccxtpro
from collections import defaultdict
import logging

logger = logging.getLogger(__name__)

class DataStream:
    """
    Abonnement WS OHLCV & OrderBook pour(ccxt.pro).
    """
    def __init__(self, exchanges: list, symbols: list, timeframe: str):
        self.exchanges  = {name: getattr(ccxtpro, name)() for name in exchanges}
        self.symbols    = symbols
        self.timeframe  = timeframe
        self.ohlcv      = defaultdict(dict)
        self.orderbooks = defaultdict(dict)

    async def start(self, on_update):
        logger.info(f"[DataStream] Exchanges : {list(self.exchanges.keys())}")
        # charge les marchés
        for name, ex in self.exchanges.items():
            await ex.load_markets()
            logger.info(f"[{name}] marchés chargés ({len(ex.symbols)} symbols)")

        # démarrage des watchers
        logger.info(f"[DataStream] Watchers pour : {self.symbols}")
        tasks = []
        for name, ex in self.exchanges.items():
            for symbol in self.symbols:
                if symbol not in ex.symbols:
                    logger.warning(f"[{name}][{symbol}] non supporté, skip")
                    continue
                tasks.append(self._ws_ohlcv(name, ex, symbol, on_update))
                tasks.append(self._ws_orderbook(name, ex, symbol, on_update))
        await asyncio.gather(*tasks)

    async def _ws_ohlcv(self, name, ex, symbol, on_update):
        while True:
            try:
                candles = await ex.watch_ohlcv(symbol, self.timeframe)
                latest  = candles[-1]
                self.ohlcv[(name, symbol)] = latest
                logger.info(f"[{name}][{symbol}][OHLCV] {latest}")
                await on_update('ohlcv', name, symbol, latest)
            except Exception as e:
                logger.error(f"[{name}][{symbol}][OHLCV] error: {e}")
                await asyncio.sleep(5)

    async def _ws_orderbook(self, name, ex, symbol, on_update):
        while True:
            try:
                ob = await ex.watch_order_book(symbol)
                self.orderbooks[(name, symbol)] = ob
                logger.info(f"[{name}][{symbol}][OB] bids={len(ob['bids'])} asks={len(ob['asks'])}")
                await on_update('orderbook', name, symbol, ob)
            except Exception as e:
                logger.error(f"[{name}][{symbol}][OB] error: {e}")
                await asyncio.sleep(5)
