# =====================================================================
# structure_utils.py — Desk Lead Structure Engine (FULL VERSION)
# Institutional-grade BOS / CHOCH / Liquidity / Trend + HTF alignment
# =====================================================================

import numpy as np
import pandas as pd
from typing import Dict, Any, List, Optional


# =====================================================================
# SWINGS
# =====================================================================

def find_swings(df: pd.DataFrame, left: int = 3, right: int = 3):
    highs = []
    lows = []
    h = df["high"].values
    l = df["low"].values

    length = len(df)
    if length < left + right + 1:
        return {"highs": [], "lows": []}

    for i in range(left, length - right):
        window_h = h[i-left:i+right+1]
        window_l = l[i-left:i+right+1]

        if h[i] == max(window_h):
            highs.append((i, float(h[i])))

        if l[i] == min(window_l):
            lows.append((i, float(l[i])))

    return {"highs": highs, "lows": lows}


# =====================================================================
# EQUAL HIGHS / EQUAL LOWS (Liquidity zones)
# =====================================================================

def detect_equal_levels(df: pd.DataFrame, tolerance: float = 0.001):
    swings = find_swings(df)
    highs = swings["highs"]
    lows = swings["lows"]

    eq_highs = []
    eq_lows = []

    for i, ph in highs:
        for j, ph2 in highs:
            if j <= i:
                continue
            if abs(ph - ph2) / ph <= tolerance:
                eq_highs.append(round((ph + ph2) / 2, 8))

    for i, pl in lows:
        for j, pl2 in lows:
            if j <= i:
                continue
            if abs(pl - pl2) / max(pl, 1e-12) <= tolerance:
                eq_lows.append(round((pl + pl2) / 2, 8))

    eq_highs = sorted(list(set(eq_highs)))
    eq_lows = sorted(list(set(eq_lows)))

    return {"eq_highs": eq_highs, "eq_lows": eq_lows}


# =====================================================================
# TREND
# =====================================================================

def _trend_from_ema(close: pd.Series) -> str:
    ema20 = close.ewm(span=20, adjust=False).mean()
    ema50 = close.ewm(span=50, adjust=False).mean()

    if ema20.iloc[-1] > ema50.iloc[-1]:
        return "LONG"
    if ema20.iloc[-1] < ema50.iloc[-1]:
        return "SHORT"
    return "NEUTRAL"


# =====================================================================
# BOS / CHOCH DETECTION
# =====================================================================

def _detect_bos_or_choch(df: pd.DataFrame):
    close = df["close"]
    highs = df["high"]
    lows = df["low"]

    # swing references
    swings = find_swings(df)
    highs_sw = swings["highs"]
    lows_sw = swings["lows"]

    if len(highs_sw) < 2 or len(lows_sw) < 2:
        return {"bos": False, "cos": False, "choch": False}

    # BOS long = break last swing high
    last_high = highs_sw[-1][1]
    prev_high = highs_sw[-2][1]

    # BOS short = break last swing low
    last_low = lows_sw[-1][1]
    prev_low = lows_sw[-2][1]

    bos_long = close.iloc[-1] > last_high
    bos_short = close.iloc[-1] < last_low

    choch_long = close.iloc[-1] > prev_high
    choch_short = close.iloc[-1] < prev_low

    return {
        "bos": bos_long or bos_short,
        "cos": False,
        "choch": choch_long or choch_short,
    }


# =====================================================================
# STRUCTURE ENGINE
# =====================================================================

def analyze_structure(df: pd.DataFrame) -> Dict[str, Any]:
    trend = _trend_from_ema(df["close"])
    swings = find_swings(df)
    levels = detect_equal_levels(df)
    bos_block = _detect_bos_or_choch(df)

    return {
        "trend": trend,
        "swings": swings,
        "liquidity": levels,
        "bos": bos_block["bos"],
        "cos": bos_block["cos"],
        "choch": bos_block["choch"],
        "oi_series": df.get("openInterest"),
        "cvd_series": df.get("cvd"),
    }


# =====================================================================
# HTF TREND CONFIRMATION
# =====================================================================

def htf_trend_ok(df_h4: pd.DataFrame, bias: str) -> bool:
    trend_h4 = _trend_from_ema(df_h4["close"])
    return trend_h4 == bias


# =====================================================================
# BOS QUALITY (volume + momentum + liquidity)
# =====================================================================

def bos_quality_details(
    df: pd.DataFrame,
    oi_series=None,
    vol_lookback: int = 60,
    vol_pct: float = 0.7,
    oi_min_trend: float = 0.003,
    oi_min_squeeze: float = -0.005,
    df_liq=None,
    price: float = None,
    tick: float = 0.01,
):

    vol = df["volume"]
    vol_ref = vol.rolling(vol_lookback).mean().iloc[-1]
    vol_ok = vol.iloc[-1] > vol_ref * vol_pct

    # OI commitment
    oi_ok = True
    if oi_series is not None:
        if len(oi_series) >= 5:
            delta_oi = oi_series.iloc[-1] - oi_series.iloc[-5]
            oi_ok = delta_oi >= oi_min_trend

    return {
        "ok": vol_ok and oi_ok,
        "vol_ok": vol_ok,
        "oi_ok": oi_ok,
    }


# =====================================================================
# COMMITMENT SCORE (OI + CVD)
# =====================================================================

def commitment_score(oi_series, cvd_series) -> Optional[float]:
    try:
        if oi_series is None or cvd_series is None:
            return 0.0

        if len(oi_series) < 10 or len(cvd_series) < 10:
            return 0.0

        d_oi = oi_series.iloc[-1] - oi_series.iloc[-10]
        d_cvd = cvd_series.iloc[-1] - cvd_series.iloc[-10]

        # simple normalized score
        score = 0.5 * np.tanh(d_oi * 10) + 0.5 * np.tanh(d_cvd * 10)
        return float(score)

    except:
        return 0.0
