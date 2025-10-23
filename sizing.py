# sizing.py — taille en lots basée sur le risque $
from __future__ import annotations
import math

def lots_by_risk(entry: float, stop: float, lot_multiplier: float, lot_step: int, risk_usdt: float) -> int:
    """
    entry, stop: prix
    lot_multiplier: quantité de base-coin par lot (ex: 0.001 BTC)
    lot_step: lot minimal / pas entier
    risk_usdt: perte max souhaitée si SL touche
    """
    dist = abs(entry - stop)
    notional_per_lot = max(1e-12, entry * float(lot_multiplier))
    # perte par lot approximée ~ dist * lot_multiplier (en base-coin) * 1 (USDT)
    loss_per_lot = max(1e-10, dist * float(lot_multiplier))
    est = int(math.floor(risk_usdt / loss_per_lot))
    return max(int(lot_step), est - (est % int(lot_step)))
