import time, random, math
from typing import Dict

class RoundRobinScheduler:
    def __init__(self, cooldown_sec: int = 300, jitter_sec: int = 60):
        self.cooldown = cooldown_sec
        self.jitter = jitter_sec
        self.next_due: Dict[str, float] = {}

    def should_run(self, symbol: str) -> bool:
        now = time.time()
        due = self.next_due.get(symbol, 0.0)
        return now >= due

    def mark_ran(self, symbol: str, bias: float = 1.0):
        # bias <1 → reviens plus vite; >1 → plus lent. Bound 0.25..4x
        bias = max(0.25, min(4.0, float(bias)))
        self.next_due[symbol] = time.time() + self.cooldown * bias + random.uniform(0, self.jitter)
