# data_stream.py

import asyncio
import ccxt.pro as ccxtpro
from collections import defaultdict
import logging

logger = logging.getLogger(__name__)

class DataStream:
    """
    Gère les abonnements WebSocket OHLCV & OrderBook.
    """
    def __init__(self, exchanges: list, symbols: list, timeframe: str):
        # Instanciation des clients ccxt.pro
        self.exchanges  = {name: getattr(ccxtpro, name)() for name in exchanges}
        self.symbols    = symbols
        self.timeframe  = timeframe
        self.ohlcv      = defaultdict(dict)  # (ex, symbol) -> latest candle
        self.orderbooks = defaultdict(dict)  # (ex, symbol) -> latest orderbook

    async def start(self, on_update):
        logger.info(f"[DataStream] Exchanges : {list(self.exchanges.keys())}")
        # Charger les marchés pour reconnaître les symbols
        for name, ex in self.exchanges.items():
            await ex.load_markets()
            logger.info(f"[{name}] marchés chargés ({len(ex.symbols)} symbols)")

        logger.info(f"[DataStream] Watchers pour {self.symbols}")
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
        """
        Reçoit un flot de bougies ; conserve et notifie la dernière.
        """
        while True:
            try:
                candles = await ex.watch_ohlcv(symbol, self.timeframe)
                latest  = candles[-1]  # [ts, open, high, low, close, vol]
                self.ohlcv[(name, symbol)] = latest
                logger.info(f"[{name}][{symbol}][OHLCV] reçu bougie {latest}")
                await on_update('ohlcv', name, symbol, latest)
            except Exception as e:
                logger.error(f"[{name}][{symbol}][OHLCV] error: {e}")
                await asyncio.sleep(5)

    async def _ws_orderbook(self, name, ex, symbol, on_update):
        """
        Reçoit un flot continu de carnets d'ordres.
        """
        while True:
            try:
                ob = await ex.watch_order_book(symbol)
                self.orderbooks[(name, symbol)] = ob
                logger.info(f"[{name}][{symbol}][OB] bids={len(ob['bids'])} asks={len(ob['asks'])}")
                await on_update('orderbook', name, symbol, ob)
            except Exception as e:
                logger.error(f"[{name}][{symbol}][OB] error: {e}")
                await asyncio.sleep(5)
