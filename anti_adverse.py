import time, logging
from dataclasses import dataclass

log = logging.getLogger("anti")

@dataclass
class QuoteState:
    side: str
    price: float
    placed_at: float
    last_touch: float

class AntiAdverse:
    def __init__(self, max_stale_sec=8.0):
        self.q: dict[str, QuoteState] = {}
        self.max_stale = max_stale_sec

    def on_book_tick(self, symbol: str, bid: float, ask: float):
        st = self.q.get(symbol)
        if not st: return None
        now = time.time()
        if st.side == "long":
            # if our bid is now at-touch but last trades were sells → risk of being picked off → pull slightly
            if bid >= st.price and (now - st.last_touch) > 1.0:
                st.last_touch = now
                return ("reprice", st.price * 0.999)
        else:
            if ask <= st.price and (now - st.last_touch) > 1.0:
                st.last_touch = now
                return ("reprice", st.price * 1.001)
        if now - st.placed_at > self.max_stale:
            return ("refresh", None)
        return None

    def on_submit(self, symbol: str, side: str, price: float):
        self.q[symbol] = QuoteState(side=side, price=price, placed_at=time.time(), last_touch=time.time())
