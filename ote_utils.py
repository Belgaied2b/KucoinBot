# ote_utils.py — détection OTE (62%–79%) du dernier swing propre (H1 par défaut)
from __future__ import annotations
import pandas as pd
from typing import Optional, Tuple

def _last_clean_swing(df: pd.DataFrame, lb:int=60) -> Optional[Tuple[float,float]]:
    # swing entre dernier plus haut et plus bas pertinents (hors bougie en cours)
    h = df["high"].astype(float).iloc[-lb-1:-1]
    l = df["low"].astype(float).iloc[-lb-1:-1]
    if h.empty or l.empty:
        return None
    sw_hi = float(h.max())
    sw_lo = float(l.min())
    if sw_hi <= sw_lo:
        return None
    return sw_lo, sw_hi

def compute_ote_zone(df: pd.DataFrame, bias: str, lb:int=60) -> Optional[Tuple[float,float]]:
    swing = _last_clean_swing(df, lb=lb)
    if not swing:
        return None
    lo, hi = swing
    bias = str(bias).upper()
    # OTE = zone idéale du retracement 62%-79% du swing précédent
    if bias == "LONG":
        retr62 = hi - 0.62*(hi-lo)
        retr79 = hi - 0.79*(hi-lo)
        low, high = sorted([retr79, retr62])
    else:
        retr62 = lo + 0.62*(hi-lo)
        retr79 = lo + 0.79*(hi-lo)
        low, high = sorted([retr62, retr79], reverse=False)
    return float(low), float(high)
