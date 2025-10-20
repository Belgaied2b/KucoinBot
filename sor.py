import logging
from dataclasses import dataclass
from typing import Optional, Dict
from core.config import SOR_ENABLE_KUCOIN, SOR_ENABLE_BINANCE, SOR_ENABLE_OKX, SOR_ENABLE_BYBIT
log = logging.getLogger("sor")

@dataclass
class VenueQuote:
    venue: str
    bid: float
    ask: float

class SOR:
    def __init__(self):
        self.best: Dict[str, VenueQuote] = {}

    def update(self, symbol: str, venue: str, bid: float, ask: float):
        vq = self.best.get(symbol)
        if vq is None:
            self.best[symbol] = VenueQuote(venue, bid, ask); return
        # keep the best spread/price for our side selection; here simple: best ask for buys, best bid for sells
        if ask < vq.ask: self.best[symbol] = VenueQuote(venue, bid, ask)

    def choose_venue_for_buy(self, symbol: str) -> Optional[str]:
        vq = self.best.get(symbol)
        if not vq: return "kucoin" if SOR_ENABLE_KUCOIN else None
        return vq.venue
