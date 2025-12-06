# =====================================================================
# structure_utils.py — Structure de marché institutionnelle (BOS/CHOCH/COS)
# Optimisé pour analyze_signal, stops, tp_utils et moteur Bitget
# =====================================================================

import numpy as np
import pandas as pd
from typing import List, Dict, Optional, Any


# ---------------------------------------------------------------------
# BASIC SWINGS (pivot highs/lows)
# ---------------------------------------------------------------------
def find_swings(df: pd.DataFrame, left: int = 2, right: int = 2) -> Dict[str, List[int]]:
    """
    Détecte les swings highs / lows robustes.
    left/right = nombre de bougies de chaque côté.
    """

    highs = []
    lows = []

    high = df["high"].values
    low = df["low"].values

    length = len(df)
    if length < left + right + 3:
        return {"highs": [], "lows": []}

    for i in range(left, length - right):
        if all(high[i] > high[i - (j + 1)] for j in range(left)) and \
           all(high[i] > high[i + (j + 1)] for j in range(right)):
            highs.append(i)

        if all(low[i] < low[i - (j + 1)] for j in range(left)) and \
           all(low[i] < low[i + (j + 1)] for j in range(right)):
            lows.append(i)

    return {"highs": highs, "lows": lows}


# ---------------------------------------------------------------------
# LIQUIDITY ZONES (Equal Highs / Equal Lows)
# ---------------------------------------------------------------------
def detect_equal_levels(df: pd.DataFrame, tolerance: float = 0.0015) -> Dict[str, List[int]]:
    """
    Détecte equal highs / equal lows avec tolérance relative.
    tolerance = 0.0015 → 0.15%
    """

    eqh = []
    eql = []

    high = df["high"].values
    low = df["low"].values
    length = len(df)

    for i in range(1, length - 1):
        if abs(high[i] - high[i - 1]) / max(high[i], 1e-8) <= tolerance:
            eqh.append(i)

        if abs(low[i] - low[i - 1]) / max(low[i], 1e-8) <= tolerance:
            eql.append(i)

    return {"equal_highs": eqh, "equal_lows": eql}


# ---------------------------------------------------------------------
# TREND DIRECTION (HH/HL / LH/LL)
# ---------------------------------------------------------------------
def detect_trend(df: pd.DataFrame, swings: Dict[str, List[int]]) -> str:
    """
    Trend = LONG si HH / HL
            SHORT si LH / LL
            NEUTRAL sinon
    """

    highs = swings["highs"]
    lows = swings["lows"]

    if len(highs) < 2 or len(lows) < 2:
        return "NEUTRAL"

    h0, h1 = highs[-2], highs[-1]
    l0, l1 = lows[-2], lows[-1]

    hh = df["high"].iloc[h1] > df["high"].iloc[h0]
    ll = df["low"].iloc[l1] < df["low"].iloc[l0]

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
    BOS = cassure avec clôture au-dessus (UP) ou en-dessous (DOWN)
    """

    highs = swings["highs"]
    lows = swings["lows"]

    if len(highs) < 2 or len(lows) < 2:
        return None

    close_val = df["close"].iloc[-1]

    # Bullish BOS → cassure du dernier swing high
    last_high = highs[-2]
    if close_val > df["high"].iloc[last_high]:
        return {
            "type": "BOS_UP",
            "level": float(df["high"].iloc[last_high]),
            "index": last_high,
        }

    # Bearish BOS → cassure du dernier swing low
    last_low = lows[-2]
    if close_val < df["low"].iloc[last_low]:
        return {
            "type": "BOS_DOWN",
            "level": float(df["low"].iloc[last_low]),
            "index": last_low,
        }

    return None


# ---------------------------------------------------------------------
# CHOCH — Change of Character
# ---------------------------------------------------------------------
def detect_choch(df: pd.DataFrame, swings: Dict[str, List[int]]) -> Optional[Dict[str, Any]]:
    """
    CHOCH = BOS dans la direction opposée à la tendance.
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
    COS = BOS dans la direction de la tendance.
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
# HTF CONFIRMATION (H4 Trend)
# ---------------------------------------------------------------------
def htf_confirm(htf_df: pd.DataFrame) -> str:
    swings = find_swings(htf_df)
    return detect_trend(htf_df, swings)


# ---------------------------------------------------------------------
# MASTER STRUCTURE ANALYSIS
# ---------------------------------------------------------------------
def analyze_structure(df: pd.DataFrame) -> Dict[str, Any]:
    """
    Résumé complet pour analyze_signal.py
    """

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
