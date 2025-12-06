# =====================================================================
# structure_utils.py — Desk Lead Bitget v1.0
# Structure de marché institutionnelle : BOS, CHoCH, COS, trend,
# swing points, HTF confirmation, BOS quality scoring, commitment OI/CVD.
# =====================================================================

import numpy as np
import pandas as pd
from typing import Dict, Any, Optional


# =====================================================================
# Swing points (détection robuste)
# =====================================================================

def _is_swing_high(df: pd.DataFrame, i: int) -> bool:
    if i < 2 or i > len(df) - 3:
        return False
    return df["high"].iloc[i] > df["high"].iloc[i-1] and df["high"].iloc[i] > df["high"].iloc[i+1]


def _is_swing_low(df: pd.DataFrame, i: int) -> bool:
    if i < 2 or i > len(df) - 3:
        return False
    return df["low"].iloc[i] < df["low"].iloc[i-1] and df["low"].iloc[i] < df["low"].iloc[i+1]


def _extract_swings(df: pd.DataFrame):
    highs, lows = [], []
    for i in range(2, len(df) - 2):
        if _is_swing_high(df, i):
            highs.append((i, df["high"].iloc[i]))
        if _is_swing_low(df, i):
            lows.append((i, df["low"].iloc[i]))
    return highs, lows


# =====================================================================
# Trend (EMA-based)
# =====================================================================

def _trend_ema(df: pd.DataFrame) -> str:
    if len(df) < 60:
        return "NEUTRAL"

    close = df["close"]
    ema21 = close.ewm(span=21, adjust=False).mean()
    ema50 = close.ewm(span=50, adjust=False).mean()

    if ema21.iloc[-1] > ema50.iloc[-1]:
        return "LONG"
    if ema21.iloc[-1] < ema50.iloc[-1]:
        return "SHORT"
    return "NEUTRAL"


# =====================================================================
# BOS / CHoCH / COS
# =====================================================================

def _detect_bos(df: pd.DataFrame, highs, lows, bias: str):
    """
    Break of Structure :
        LONG  -> break swing high
        SHORT -> break swing low
    """
    if not highs or not lows:
        return None

    last_close = df["close"].iloc[-1]

    if bias == "LONG":
        last_high = highs[-1][1]
        if last_close > last_high:
            return {"type": "BOS", "at": highs[-1][0], "level": last_high}

    if bias == "SHORT":
        last_low = lows[-1][1]
        if last_close < last_low:
            return {"type": "BOS", "at": lows[-1][0], "level": last_low}

    return None


def _detect_choch(df: pd.DataFrame, highs, lows, bias: str):
    """
    Change of Character :
        LONG  -> break previous LOW
        SHORT -> break previous HIGH
    """
    if len(highs) < 2 or len(lows) < 2:
        return None

    last_close = df["close"].iloc[-1]

    # CHoCH to LONG: break last swing HIGH while previous trend was SHORT
    if bias == "LONG":
        key_low = lows[-1][1]
        if last_close < key_low:
            return {"type": "CHoCH", "level": key_low}

    # CHoCH to SHORT: break last swing LOW while previous trend was LONG
    if bias == "SHORT":
        key_high = highs[-1][1]
        if last_close > key_high:
            return {"type": "CHoCH", "level": key_high}

    return None


def _detect_cos(df: pd.DataFrame, highs, lows, bias: str):
    """
    COS = Change of State
        LONG  → prix ne casse plus les LH
        SHORT → prix ne casse plus les HL
    """
    if len(highs) < 2 or len(lows) < 2:
        return None

    last_close = df["close"].iloc[-1]

    if bias == "LONG":
        prev_high = highs[-1][1]
        if last_close < prev_high:
            return {"type": "COS", "level": prev_high}

    if bias == "SHORT":
        prev_low = lows[-1][1]
        if last_close > prev_low:
            return {"type": "COS", "level": prev_low}

    return None


# =====================================================================
# HTF Trend confirmation
# =====================================================================

def htf_trend_ok(df_h4: pd.DataFrame, bias: str) -> bool:
    """HTF (H4) must confirm H1 bias."""
    t = _trend_ema(df_h4)
    return t == bias


# =====================================================================
# BOS Quality (institutionnel)
# =====================================================================

def bos_quality_details(
    df: pd.DataFrame,
    oi_series=None,
    vol_lookback: int = 60,
    vol_pct: float = 0.8,
    oi_min_trend: float = 0.003,
    oi_min_squeeze: float = -0.005,
    df_liq=None,
    price: float = None,
    tick: float = 0.01,
) -> Dict[str, Any]:

    out = {
        "ok": True,
        "volume_ok": True,
        "oi_ok": True,
        "liquidity_ok": True,
        "details": {},
    }

    # -----------------------------
    # Volume expansion
    # -----------------------------
    try:
        vol = df["volume"].iloc[-1]
        avg = df["volume"].rolling(vol_lookback).mean().iloc[-1]
        out["volume_ok"] = bool(vol > avg * (1 + vol_pct))
        out["details"]["volume"] = f"{vol:.2f}/{avg:.2f}"
    except:
        out["volume_ok"] = True

    # -----------------------------
    # Open Interest trend
    # -----------------------------
    if oi_series is not None and len(oi_series) > 10:
        delta_oi = oi_series.iloc[-1] - oi_series.iloc[-10]
        if delta_oi < oi_min_squeeze:
            out["oi_ok"] = False
        out["details"]["oi_delta"] = float(delta_oi)

    # -----------------------------
    # Liquidité (EQ highs/lows)
    # -----------------------------
    # Si tu veux activer une logique type “grab/mitigation”, j’ajouterai ici.
    out["liquidity_ok"] = True

    # -----------------------------
    # Final decision
    # -----------------------------
    out["ok"] = out["volume_ok"] and out["oi_ok"] and out["liquidity_ok"]
    return out


# =====================================================================
# Commitment Score
# =====================================================================

def commitment_score(oi_series, cvd_series) -> Optional[float]:
    """
    Score institutionnel (0–1) basé sur unanimité OI + CVD.
    """
    try:
        if oi_series is None or cvd_series is None:
            return None
        if len(oi_series) < 10 or len(cvd_series) < 10:
            return None

        oi_delta = oi_series.iloc[-1] - oi_series.iloc[-8]
        cvd_delta = cvd_series.iloc[-1] - cvd_series.iloc[-8]

        # Normalisation simple (logique Desk Lead)
        score = 0.5 * np.tanh(oi_delta * 5) + 0.5 * np.tanh(cvd_delta * 5)
        return float((score + 1) / 2)
    except:
        return None


# =====================================================================
# Full Structure Analyzer
# =====================================================================

def analyze_structure(df: pd.DataFrame) -> Dict[str, Any]:
    """
    Retourne :
        {
          trend: LONG/SHORT/NEUTRAL,
          bos: {…} | None,
          choch: {…} | None,
          cos: {…} | None,
          swing_highs: [...],
          swing_lows: [...],
          oi_series: None (hook pour analyze_signal),
          cvd_series: None,
        }
    """

    highs, lows = _extract_swings(df)
    bias = _trend_ema(df)

    bos = _detect_bos(df, highs, lows, bias)
    choch = _detect_choch(df, highs, lows, bias)
    cos = _detect_cos(df, highs, lows, bias)

    return {
        "trend": bias,
        "bos": bos,
        "choch": choch,
        "cos": cos,
        "swing_highs": highs,
        "swing_lows": lows,
        # OI/CVD filled later by institutional_data
        "oi_series": None,
        "cvd_series": None,
    }
