from __future__ import annotations
import math, statistics
from typing import List
import numpy as np

def _clean(vals: List[float]) -> List[float]:
    out=[]
    for v in vals:
        try:
            x=float(v)
            if math.isfinite(x): out.append(x)
        except Exception:
            pass
    return out

def pct_change(seq: List[float]) -> List[float]:
    out = []
    seq = _clean(seq)
    for i in range(1, len(seq)):
        a, b = seq[i-1], seq[i]
        out.append((b - a) / a if a else 0.0)
    return out

def cvd_from_klines(kl: List[List]) -> float:
    try:
        deltas = []
        for k in kl[-180:]:
            o, c, v = float(k[1]), float(k[4]), float(k[5])
            direction = 1.0 if c >= o else -1.0
            deltas.append(direction * v * abs(c - o))
        return float(sum(deltas))
    except Exception:
        return 0.0

def oi_delta_strength(oi_hist: List[float]) -> float:
    if len(oi_hist) < 10: return 0.0
    ch = pct_change(oi_hist)
    slope = (oi_hist[-1] - oi_hist[0]) / max(abs(oi_hist[0]), 1e-9)
    return float(0.6 * slope + 0.4 * (ch[-1] if ch else 0.0))

def funding_score(fr: List[float]) -> float:
    fr = _clean(fr)
    if not fr: return 0.0
    m = statistics.mean(fr)
    s = statistics.pstdev(fr) or 1e-9
    return abs(m / s)

def liq_stress(lsr: List[float]) -> float:
    if len(lsr) < 10: return 0.0
    x = lsr[-1]
    med = float(np.median(lsr))
    iqr = float(np.percentile(lsr, 75) - np.percentile(lsr, 25)) or 1e-9
    z = (x - med) / iqr
    return float(max(-4.0, min(4.0, z)))
