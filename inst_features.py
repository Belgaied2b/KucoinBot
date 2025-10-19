# inst_features.py
from __future__ import annotations
import math, statistics
from typing import List
import numpy as np

def _safe(vals: List[float]) -> List[float]:
    return [float(x) for x in vals if x is not None and not (isinstance(x, float) and (math.isnan(x) or math.isinf(x)))]

def pct_change(seq: List[float]) -> List[float]:
    out = []
    for i in range(1, len(seq)):
        a, b = seq[i-1], seq[i]
        out.append((b - a) / a if a else 0.0)
    return out

def zscore(x: float, mean: float, std: float, cap: float = 4.0) -> float:
    if std <= 1e-12: return 0.0
    z = (x - mean) / std
    return max(-cap, min(cap, z))

def robust_scale(x: float, med: float, iqr: float, cap: float = 4.0) -> float:
    if iqr <= 1e-12: return 0.0
    r = (x - med) / iqr
    return max(-cap, min(cap, r))

def cvd_from_klines(kl: List[List]) -> float:
    try:
        deltas = []
        for k in kl[-120:]:
            o, h, l, c, v = float(k[1]), float(k[2]), float(k[3]), float(k[4]), float(k[5])
            direction = 1.0 if c >= o else -1.0
            deltas.append(direction * v * abs(c - o))
        return float(sum(deltas))
    except Exception:
        return 0.0

def oi_delta_strength(oi_hist: List[float]) -> float:
    if len(oi_hist) < 5: return 0.0
    ch = pct_change(_safe(oi_hist))
    slope = (oi_hist[-1] - oi_hist[0]) / max(abs(oi_hist[0]), 1e-9)
    return float(0.6 * slope + 0.4 * (ch[-1] if ch else 0.0))

def funding_score(fr: List[float]) -> float:
    if not fr: return 0.0
    m = statistics.mean(fr)
    s = statistics.pstdev(fr) or 1e-9
    return abs(m / s)

def liq_stress(lsr: List[float]) -> float:
    if len(lsr) < 5: return 0.0
    x = lsr[-1]
    med = float(np.median(lsr))
    iqr = float(np.percentile(lsr, 75) - np.percentile(lsr, 25)) or 1e-9
    return abs(robust_scale(x, med, iqr))

def normalize_01(val: float, max_ref: float) -> float:
    return max(0.0, min(1.0, 0.5 + val / max(1e-9, max_ref) / 2.0))
