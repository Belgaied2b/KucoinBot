import time, logging
from collections import defaultdict
from typing import Dict
log = logging.getLogger("cvd")

class CVDMux:
    def __init__(self, half_life_sec: float = 900.0):
        self.cvd: Dict[str, float] = defaultdict(float)
        self.last_ts: Dict[str, float] = {}
        self.half = half_life_sec
        self.lmbd = 0.693/half_life_sec if half_life_sec>0 else 0.0

    def on_trade(self, venue_sym: str, price: float, qty: float, is_sell: bool):
        ts = time.time()
        side = -1.0 if is_sell else 1.0
        delta = side * qty
        prev_ts = self.last_ts.get(venue_sym, ts)
        dt = ts - prev_ts
        if dt>0 and self.lmbd>0:
            decay = pow(2.0, -dt/self.half)
            self.cvd[venue_sym] *= decay
        self.cvd[venue_sym] += delta
        self.last_ts[venue_sym]=ts

    def value(self, venue_sym: str) -> float:
        return float(self.cvd.get(venue_sym, 0.0))
