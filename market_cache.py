import time, logging
from typing import Dict, Any, Tuple
from exchanges.kucoin_adapter import fetch_klines

log = logging.getLogger("mkt.cache")

class MarketCache:
    def __init__(self, h1_ttl: int, h4_ttl: int):
        self.h1_ttl = h1_ttl
        self.h4_ttl = h4_ttl
        self._h1: Dict[str, Tuple[float, Any]] = {}
        self._h4: Dict[str, Tuple[float, Any]] = {}

    def _is_fresh(self, ts: float, ttl: int) -> bool:
        return (time.time() - ts) < ttl

    def get_h1(self, symbol: str):
        ts, df = self._h1.get(symbol, (0.0, None))
        if df is not None and self._is_fresh(ts, self.h1_ttl):
            return df
        df = fetch_klines(symbol, "1h", 500)
        self._h1[symbol] = (time.time(), df)
        return df

    def get_h4(self, symbol: str):
        ts, df = self._h4.get(symbol, (0.0, None))
        if df is not None and self._is_fresh(ts, self.h4_ttl):
            return df
        df = fetch_klines(symbol, "4h", 400)
        self._h4[symbol] = (time.time(), df)
        return df
