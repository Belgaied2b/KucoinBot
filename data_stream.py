import asyncio
import ccxt.pro as ccxtpro
from collections import defaultdict
import logging

logger = logging.getLogger(__name__)

class DataStream:
    """
    Gère les flux WebSocket OHLCV et orderbook pour un ou plusieurs exchanges.
    """
    def __init__(self, exchanges: list, symbols: list, timeframe: str):
        # Instancie les clients ccxt.pro correspondants
        self.exchanges   = {name: getattr(ccxtpro, name)() for name in exchanges}
        self.symbols     = symbols
        self.timeframe   = timeframe
        self.ohlcv       = defaultdict(dict)  # (exchange, symbol) -> latest candle
        self.orderbooks  = defaultdict(dict)  # (exchange, symbol) -> latest orderbook

    async def start(self, on_update):
        logger.info(f"[DataStream] Initialisation des exchanges : {list(self.exchanges.keys())}")
        # Charger les marchés pour chaque exchange
        for name, ex in self.exchanges.items():
            await ex.load_markets()
            logger.info(f"[{name}] marchés chargés ({len(ex.symbols)} symboles)")
        logger.info(f"[DataStream] Démarrage des WebSocket watchers pour {self.symbols}")

        # Lancer un watcher OHLCV et OB par symbole/support
        tasks = []
        for name, ex in self.exchanges.items():
            for symbol in self.symbols:
                if symbol not in ex.symbols:
                    logger.warning(f"[{name}][{symbol}] symbole non supporté, skip")
                    continue
                tasks.append(self._ws_ohlcv(name, ex, symbol, on_update))
                tasks.append(self._ws_orderbook(name, ex, symbol, on_update))

        await asyncio.gather(*tasks)

    async def _ws_ohlcv(self, name, ex, symbol, on_update):
        """
        Récupère un flux de bougies, conserve et notifie uniquement la dernière.
        """
        while True:
            try:
                candles = await ex.watch_ohlcv(symbol, self.timeframe)
                latest  = candles[-1]  # [timestamp, open, high, low, close, volume]
                self.ohlcv[(name, symbol)] = latest
                await on_update('ohlcv', name, symbol, latest)
            except Exception as e:
                logger.error(f"[{name}][{symbol}][OHLCV] error: {e}")
                await asyncio.sleep(5)

    async def _ws_orderbook(self, name, ex, symbol, on_update):
        """
        Récupère un flux continu de carnets d'ordres.
        """
        while True:
            try:
                ob = await ex.watch_order_book(symbol)
                self.orderbooks[(name, symbol)] = ob
                await on_update('orderbook', name, symbol, ob)
            except Exception as e:
                logger.error(f"[{name}][{symbol}][OB] error: {e}")
                await asyncio.sleep(5)
