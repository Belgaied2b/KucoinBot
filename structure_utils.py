# =====================================================================
# structure_utils.py — Structure de marché institutionnelle (BOS/CHOCH/COS)
# =====================================================================
import numpy as np
import pandas as pd
from typing import List, Dict, Optional


# ---------------------------------------------------------------------
# BASIC SWINGS (pivot highs/lows)
# ---------------------------------------------------------------------
def find_swings(df: pd.DataFrame, left: int = 2, right: int = 2) -> Dict[str, List[int]]:
    """
    Détecte les swings highs / lows simples.
    left/right = nombre de bougies de chaque côté.
    """
    highs = []
    lows = []

    high = df["high"].values
    low = df["low"].values

    for i in range(left, len(df) - right):
        if all(high[i] > high[i - j - 1] for j in range(left)) and \
           all(high[i] > high[i + j + 1] for j in range(right)):
            highs.append(i)

        if all(low[i] < low[i - j - 1] for j in range(left)) and \
           all(low[i] < low[i + j + 1] for j in range(right)):
            lows.append(i)

    return {"highs": highs, "lows": lows}


# ---------------------------------------------------------------------
# EQUAL HIGHS / EQUAL LOWS — LIQUIDITY ZONES
# ---------------------------------------------------------------------
def detect_equal_levels(df: pd.DataFrame, tolerance: float = 0.0015) -> Dict[str, List[int]]:
    """
    Trouve equal highs / equal lows dans une tolérance relative.
    tolerance = 0.0015 → 0.15%
    """
    eqh = []
    eql = []

    high = df["high"].values
    low = df["low"].values

    for i in range(2, len(df) - 2):
        # Detect Equal Highs
        if abs(high[i] - high[i - 1]) / max(high[i], 1e-8) <= tolerance:
            eqh.append(i)
        # Detect Equal Lows
        if abs(low[i] - low[i - 1]) / max(low[i], 1e-8) <= tolerance:
            eql.append(i)

    return {"equal_highs": eqh, "equal_lows": eql}


# ---------------------------------------------------------------------
# Trend direction simple (higher-high / lower-low)
# ---------------------------------------------------------------------
def detect_trend(df: pd.DataFrame, swings: Dict[str, List[int]]) -> str:
    """
    Trend = LONG si structure monte (HH / HL)
            SHORT si structure descend (LH / LL)
            NEUTRAL si indéfini
    """

    highs = swings["highs"]
    lows = swings["lows"]

    if len(highs) < 2 or len(lows) < 2:
        return "NEUTRAL"

    last_highs = highs[-2:]
    last_lows = lows[-2:]

    hh = df["high"].iloc[last_highs[1]] > df["high"].iloc[last_highs[0]]
    ll = df["low"].iloc[last_lows[1]] < df["low"].iloc[last_lows[0]]

    if hh and not ll:
        return "LONG"
    if ll and not hh:
        return "SHORT"
    return "NEUTRAL"


# ---------------------------------------------------------------------
# BOS — Break Of Structure
# ---------------------------------------------------------------------
def detect_bos(df: pd.DataFrame, swings: Dict[str, List[int]]) -> Optional[Dict[str, Any]]:
    """
    BOS = cassure d'un swing récent avec clôture au-dessus/dessous.
    """
    highs = swings["highs"]
    lows = swings["lows"]

    if len(highs) < 2 or len(lows) < 2:
        return None

    close = df["close"].values

    # Break up (bullish BOS)
    last_high = highs[-2]
    if close[-1] > df["high"].iloc[last_high]:
        return {"type": "BOS_UP", "level": df["high"].iloc[last_high], "index": last_high}

    # Break down (bearish BOS)
    last_low = lows[-2]
    if close[-1] < df["low"].iloc[last_low]:
        return {"type": "BOS_DOWN", "level": df["low"].iloc[last_low], "index": last_low}

    return None


# ---------------------------------------------------------------------
# CHOCH — Change of Character
# ---------------------------------------------------------------------
def detect_choch(df: pd.DataFrame, swings: Dict[str, List[int]]) -> Optional[Dict[str, Any]]:
    """
    CHOCH = BOS dans la direction opposée du trend.
    """

    trend = detect_trend(df, swings)
    bos = detect_bos(df, swings)
    if not bos:
        return None

    if trend == "LONG" and bos["type"] == "BOS_DOWN":
        return {"type": "CHOCH_DOWN", "bos": bos}

    if trend == "SHORT" and bos["type"] == "BOS_UP":
        return {"type": "CHOCH_UP", "bos": bos}

    return None


# ---------------------------------------------------------------------
# COS — Continuation Of Structure
# ---------------------------------------------------------------------
def detect_cos(df: pd.DataFrame, swings: Dict[str, List[int]]) -> Optional[Dict[str, Any]]:
    """
    COS = cassure dans la direction du trend.
    """

    trend = detect_trend(df, swings)
    bos = detect_bos(df, swings)
    if not bos:
        return None

    if trend == "LONG" and bos["type"] == "BOS_UP":
        return {"type": "COS_UP", "bos": bos}

    if trend == "SHORT" and bos["type"] == "BOS_DOWN":
        return {"type": "COS_DOWN", "bos": bos}

    return None


# ---------------------------------------------------------------------
# HTF trend confirmation (e.g. H4)
# ---------------------------------------------------------------------
def htf_confirm(htf_df: pd.DataFrame) -> str:
    swings = find_swings(htf_df)
    return detect_trend(htf_df, swings)


# ---------------------------------------------------------------------
# Structure summary for analyze_signal
# ---------------------------------------------------------------------
def analyze_structure(df: pd.DataFrame) -> Dict[str, Any]:
    swings = find_swings(df)
    trend = detect_trend(df, swings)
    bos = detect_bos(df, swings)
    choch = detect_choch(df, swings)
    cos = detect_cos(df, swings)
    liquidity = detect_equal_levels(df)

    return {
        "trend": trend,
        "bos": bos,
        "choch": choch,
        "cos": cos,
        "liquidity": liquidity,
        "swings": swings,
    }
