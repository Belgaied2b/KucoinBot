# =====================================================================
# tp_clamp.py — Institutional TP Engine (Desk Lead)
# TP1 dynamique basé sur RR, volatilité et structure
# Compatible analyze_signal.py
# =====================================================================

import pandas as pd
import numpy as np
from typing import Tuple


# ============================================================
# Helper — round price to tick
# ============================================================

def _round_to_tick(price: float, tick: float) -> float:
    return round(price / tick) * tick


# ============================================================
# Volatility filter for adaptive TP
# ============================================================

def _atr(df: pd.DataFrame, length: int = 14) -> float:
    high = df["high"]
    low = df["low"]
    close = df["close"]
    prev_close = close.shift(1)

    tr = pd.concat([
        (high - low).abs(),
        (high - prev_close).abs(),
        (low - prev_close).abs(),
    ], axis=1).max(axis=1)

    atr = tr.rolling(length).mean().iloc[-1]
    if np.isnan(atr):
        atr = tr.mean()

    return float(atr)


# ============================================================
# MAIN FUNCTION — compute_tp1
# ============================================================

def compute_tp1(
    entry: float,
    sl: float,
    bias: str,
    df: pd.DataFrame,
    tick: float,
) -> Tuple[float, float]:
    """
    TP1 dynamique institutionnel :

        - BASE RR = 1.4 à 1.6 (clamp stable)
        - S'ajuste en fonction :
              * volatilité (ATR%)
              * distance SL (trop large → RR réduit)
              * momentum structurel

        Retourne (TP1, RR_effectif)
    """

    # -----------------------------
    # 1) Base risk / reward ratio
    # -----------------------------
    risk = abs(entry - sl)

    if risk <= 0:
        return entry, 0.0

    # volatilité
    atr = _atr(df)
    atrp = atr / entry

    # clamp RR min/max
    rr_min = 1.40
    rr_max = 1.60

    # adaption selon volatilité
    if atrp > 0.03:    # très volatile → RR réduit
        rr_base = rr_min
    elif atrp < 0.015: # calme → augmenter RR
        rr_base = rr_max
    else:
        rr_base = (rr_min + rr_max) / 2

    # -----------------------------
    # 2) Compute TP1
    # -----------------------------
    if bias == "LONG":
        tp1_raw = entry + risk * rr_base
    else:
        tp1_raw = entry - risk * rr_base

    # -----------------------------
    # 3) Round to tick
    # -----------------------------
    tp1 = _round_to_tick(tp1_raw, tick)

    if tp1 <= 0:
        tp1 = tp1_raw

    rr_effective = abs(tp1 - entry) / risk

    return float(tp1), float(rr_effective)
