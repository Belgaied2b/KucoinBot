import collections, time
from typing import Dict

class LiqHeuristic:
    def __init__(self, half_life_sec=600.0):
        self.score: Dict[str, float] = collections.defaultdict(float)
        self.last: Dict[str, float] = {}
        self.half = half_life_sec

    def on_trade(self, sym: str, price: float, qty: float, is_sell: bool):
        # very crude: large aggressive prints move score
        impact = qty ** 0.5
        self.score[sym] = self.score.get(sym,0.0)*0.5 + impact

    def value(self, sym: str) -> float:
        return float(self.score.get(sym, 0.0))
