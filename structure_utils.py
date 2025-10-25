import pandas as pd
import numpy as np
from typing import Optional

# === Structure de base : BOS / Validation structurelle ===

def detect_bos(df: pd.DataFrame, lookback=10):
    highs = df["high"].rolling(lookback).max()
    lows = df["low"].rolling(lookback).min()
    if df["close"].iloc[-1] > (highs.iloc[-2] if len(df) > 2 else df["high"].iloc[-2]):
        return "BOS_UP"
    if df["close"].iloc[-1] < (lows.iloc[-2] if len(df) > 2 else df["low"].iloc[-2]):
        return "BOS_DOWN"
    return None

def structure_valid(df: pd.DataFrame, bias: str, lookback=10) -> bool:
    bos = detect_bos(df, lookback)
    return (bias == "LONG" and bos == "BOS_UP") or (bias == "SHORT" and bos == "BOS_DOWN")


# === Extensions institutionnelles : HTF alignment / BOS quality / Commitment score ===

def _ema(series: pd.Series, span: int) -> pd.Series:
    return series.ewm(span=span, adjust=False).mean()

def htf_trend_ok(df_htf: Optional[pd.DataFrame], bias: str) -> bool:
    """
    Aligne le trade 1H dans la tendance 4H/D1:
      LONG  : close > EMA50 et EMA20 > EMA50
      SHORT : close < EMA50 et EMA20 < EMA50
    Si df_htf indisponible ou trop court -> True (ne bloque pas).
    """
    if df_htf is None or len(df_htf) < 60:
        return True
    close = df_htf["close"].astype(float)
    ema20 = _ema(close, 20)
    ema50 = _ema(close, 50)
    if str(bias).upper() == "LONG":
        return bool(close.iloc[-1] > ema50.iloc[-1] and ema20.iloc[-1] > ema50.iloc[-1])
    return bool(close.iloc[-1] < ema50.iloc[-1] and ema20.iloc[-1] < ema50.iloc[-1])

def bos_quality_ok(df: pd.DataFrame, oi_series: Optional[pd.Series] = None,
                   vol_lookback: int = 60, vol_pct: float = 0.80,
                   oi_min_trend: float = 0.003, oi_min_squeeze: float = -0.005) -> bool:
    """
    Qualité du break (BOS/CHoCH) sur la dernière bougie:
      - Volume de la dernière bougie ≥ p80 des 'vol_lookback' dernières
      - ET variation d'OI sur ~2 barres ≥ +0.3% (apports) OU ≤ -0.5% (squeeze/delever)
    Si data manquante -> True (ne bloque pas).
    """
    if df is None or len(df) < max(5, vol_lookback):
        return True
    try:
        vol = df["volume"].astype(float).tail(vol_lookback)
        v_last = float(vol.iloc[-1])
        thresh = float(vol.quantile(vol_pct))
        vol_ok = v_last >= thresh
    except Exception:
        vol_ok = True

    oi_ok = True
    if oi_series is not None and len(oi_series) >= 3:
        try:
            oi_t0 = float(oi_series.iloc[-3])
            oi_t2 = float(oi_series.iloc[-1])
            if oi_t0 > 0:
                delta = (oi_t2 - oi_t0) / oi_t0
                oi_ok = (delta >= oi_min_trend) or (delta <= oi_min_squeeze)
        except Exception:
            oi_ok = True

    return bool(vol_ok and oi_ok)

def _slope(series: pd.Series, window: int) -> float:
    """
    Pente simple via régression linéaire (index normalisé 0..1).
    Retourne 0 si données insuffisantes.
    """
    if series is None or len(series) < window:
        return 0.0
    y = series.tail(window).astype(float).values
    x = np.linspace(0.0, 1.0, num=len(y))
    denom = (x - x.mean()).sum() ** 2 + 1e-12
    m = ((x - x.mean()) * (y - y.mean())).sum() / denom
    return float(m)

def commitment_score(oi_series: Optional[pd.Series],
                     cvd_series: Optional[pd.Series],
                     window: int = 20) -> float:
    """
    Score 0..1 de 'commitment' (intention + exécution) basé sur:
      - variation normalisée d'OI (apports / deleverage)
      - pente du CVD (agression)
    Normalisation robuste par MAD; clampé à [0, 1].
    """
    # OI change %
    oi_comp = 0.0
    if oi_series is not None and len(oi_series) >= 3:
        o = oi_series.astype(float).tail(window)
        try:
            pct = (o.iloc[-1] - o.iloc[0]) / max(1e-12, o.iloc[0])
            oi_comp = float(pct)
        except Exception:
            oi_comp = 0.0

    # CVD slope normalisée par MAD
    cvd_comp = 0.0
    if cvd_series is not None and len(cvd_series) >= window:
        c = cvd_series.astype(float).tail(window)
        m = _slope(c, window=window)
        mad = np.median(np.abs(c - np.median(c))) + 1e-12
        cvd_comp = float(m / mad)

    # combinaison et squash
    raw = 0.6 * oi_comp + 0.4 * cvd_comp
    score = 1 / (1 + np.exp(-3.5 * raw))  # logistic
    return float(np.clip(score, 0.0, 1.0))
