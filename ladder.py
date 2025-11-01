# ladder.py — ladder maker post-only dans la zone OTE
from __future__ import annotations
from typing import List, Tuple
import math

def build_ladder_prices(side:str, ote_low:float, ote_high:float, tick:float, n:int=3) -> List[float]:
    side = side.lower()
    tick = float(tick)
    low, high = float(ote_low), float(ote_high)
    if low >= high or tick <= 0 or n <= 0:
        return []
    # répartition biaisée vers le bord “cheap”
    levels = []
    for i in range(n):
        w = (i+1)/(n+1)
        p = low*(1-w) + high*w
        # biais : long proche du low, short proche du high
        if side == "long":
            p = low + (p-low)*0.6
            p -= tick  # buffer maker
        else:
            p = high - (high-p)*0.6
            p += tick
        steps = math.floor(p/tick) if side=="long" else math.ceil(p/tick)
        levels.append(round(steps*tick, 12))
    # tri côté “cheap” d’abord pour remplir bas en premier
    return sorted(set(levels), reverse=False) if side=="long" else sorted(set(levels), reverse=True)
