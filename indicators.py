# =====================================================================
# indicators.py — Desk Lead Premium Technical Suite
# Institutionnel, robuste, optimisé Bitget / Binance
# =====================================================================

from typing import Dict, Any, Optional

import numpy as np
import pandas as pd


# =====================================================================
# SAFE HELPERS
# =====================================================================

def _safe_len(obj) -> int:
    try:
        return len(obj)
    except Exception:
        return 0


def _safe_series(series, fill: float = 0.0) -> pd.Series:
    if series is None:
        return pd.Series([fill])
    return pd.Series(series).astype(float)


# =====================================================================
# EMA / SMA
# =====================================================================

def ema(series: pd.Series, length: int) -> pd.Series:
    s = _safe_series(series)
    if _safe_len(s) < 2:
        return pd.Series([np.nan])
    return s.ewm(span=length, adjust=False).mean()


def sma(series: pd.Series, length: int) -> pd.Series:
    s = _safe_series(series)
    if _safe_len(s) < length:
        return pd.Series([np.nan])
    return s.rolling(window=length).mean()


# =====================================================================
# RSI
# =====================================================================

def rsi(series: pd.Series, length: int = 14) -> pd.Series:
    s = _safe_series(series)
    if _safe_len(s) <= length:
        return pd.Series([50.0] * _safe_len(s))

    delta = s.diff()
    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)

    avg_gain = gain.rolling(length).mean()
    avg_loss = loss.rolling(length).mean()

    rs = avg_gain / avg_loss.replace(0, np.nan)
    out = 100.0 - (100.0 / (1.0 + rs))
    out = out.fillna(method="bfill").fillna(50.0)
    return out


# =====================================================================
# MACD
# =====================================================================

def macd(series: pd.Series, fast: int = 12, slow: int = 26, signal: int = 9):
    """
    Retourne (macd_line, signal_line, histogram).
    """
    s = _safe_series(series)
    ema_fast = s.ewm(span=fast, adjust=False).mean()
    ema_slow = s.ewm(span=slow, adjust=False).mean()

    macd_line = ema_fast - ema_slow
    signal_line = macd_line.ewm(span=signal, adjust=False).mean()
    hist = macd_line - signal_line
    return macd_line, signal_line, hist


# =====================================================================
# TRUE ATR
# =====================================================================

def true_atr(df: pd.DataFrame, length: int = 14) -> pd.Series:
    """
    Calcule l'Average True Range (Wilder) sur length périodes.
    """
    if df is None or _safe_len(df) < 2:
        return pd.Series([0.0])

    high = df["high"].astype(float)
    low = df["low"].astype(float)
    close = df["close"].astype(float)

    prev_close = close.shift(1)
    tr1 = high - low
    tr2 = (high - prev_close).abs()
    tr3 = (low - prev_close).abs()
    tr = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)

    atr = tr.ewm(alpha=1.0 / length, adjust=False).mean()
    return atr


# =====================================================================
# VOLATILITY REGIME
# =====================================================================

def volatility_regime(df: pd.DataFrame, length: int = 14) -> str:
    """
    Classifie le régime de volatilité:
      - "LOW"    : ATR% < 1%
      - "MEDIUM" : 1% ≤ ATR% < 3%
      - "HIGH"   : ATR% ≥ 3%
    """
    if df is None or _safe_len(df) < length + 2:
        return "UNKNOWN"

    atr = true_atr(df, length=length)
    last_atr = float(atr.iloc[-1])
    last_price = float(df["close"].iloc[-1])

    if last_price <= 0:
        return "UNKNOWN"

    atr_pct = last_atr / last_price

    if atr_pct < 0.01:
        return "LOW"
    if atr_pct < 0.03:
        return "MEDIUM"
    return "HIGH"


# =====================================================================
# OTE (Optimal Trade Entry) — simple helper
# =====================================================================

def compute_ote(df: pd.DataFrame, lookback: int = 80) -> Dict[str, Any]:
    """
    Approximation de la zone OTE (0.62–0.79 Fibo) sur la dernière jambe swing.

    Retourne :
      {
        "discount_zone": (low_ote, high_ote),
        "premium_zone": (low_ote, high_ote),
        "in_discount": bool,
        "in_premium": bool,
      }
    """
    if df is None or _safe_len(df) < lookback:
        return {
            "discount_zone": (None, None),
            "premium_zone": (None, None),
            "in_discount": False,
            "in_premium": False,
        }

    w = df.tail(lookback)
    high = float(w["high"].max())
    low = float(w["low"].min())
    last = float(w["close"].iloc[-1])

    leg_high, leg_low = high, low
    diff = leg_high - leg_low
    if diff <= 0:
        return {
            "discount_zone": (None, None),
            "premium_zone": (None, None),
            "in_discount": False,
            "in_premium": False,
        }

    # Zones fibo
    discount_low = leg_low + 0.62 * diff
    discount_high = leg_low + 0.79 * diff

    premium_low = leg_low + 0.2 * diff
    premium_high = leg_low + 0.38 * diff

    in_discount = discount_low <= last <= discount_high
    in_premium = premium_low <= last <= premium_high

    return {
        "discount_zone": (discount_low, discount_high),
        "premium_zone": (premium_low, premium_high),
        "in_discount": bool(in_discount),
        "in_premium": bool(in_premium),
    }


# =====================================================================
# INSTITUTIONAL MOMENTUM
# =====================================================================

def institutional_momentum(df: pd.DataFrame) -> str:
    """
    Score de momentum "institutionnel" basé sur :
      - MACD (signe + pente)
      - EMA20 / EMA50
      - RSI
      - Volume spike

    Retourne :
      - "STRONG_BULLISH"
      - "BULLISH"
      - "STRONG_BEARISH"
      - "BEARISH"
      - "NEUTRAL"
    """
    if df is None or _safe_len(df) < 40:
        return "NEUTRAL"

    close = df["close"].astype(float)
    volume = df["volume"].astype(float)

    r = rsi(close, length=14)
    macd_line, signal_line, hist = macd(close)
    ema20 = ema(close, 20)
    ema50 = ema(close, 50)

    # Pente MACD sur les 5 dernières barres
    if _safe_len(macd_line) >= 5:
        macd_slope = float(macd_line.iloc[-1] - macd_line.iloc[-5])
    else:
        macd_slope = 0.0

    # Volume spike (dernière barre vs moyenne 20)
    if _safe_len(volume) >= 20:
        avg_vol = float(volume.iloc[-20:].mean())
        vol_spike = float(volume.iloc[-1]) > 1.5 * avg_vol
    else:
        vol_spike = False

    last_rsi = float(r.iloc[-1])
    last_hist = float(hist.iloc[-1])
    last_ema20 = float(ema20.iloc[-1])
    last_ema50 = float(ema50.iloc[-1])

    # Forte tendance haussière
    if (
        last_hist > 0
        and macd_slope > 0
        and last_ema20 > last_ema50
        and last_rsi > 55
        and not vol_spike
    ):
        return "STRONG_BULLISH"

    # Tendance haussière modérée
    if (
        last_hist > 0
        and last_ema20 > last_ema50
        and last_rsi > 50
    ):
        return "BULLISH"

    # Forte tendance baissière
    if (
        last_hist < 0
        and macd_slope < 0
        and last_ema20 < last_ema50
        and last_rsi < 45
        and not vol_spike
    ):
        return "STRONG_BEARISH"

    # Tendance baissière modérée
    if (
        last_hist < 0
        and last_ema20 < last_ema50
        and last_rsi < 50
    ):
        return "BEARISH"

    return "NEUTRAL"


# =====================================================================
# (Optionnel) Divergence RSI simple — pour futures évolutions
# =====================================================================

def detect_rsi_divergence(df: pd.DataFrame) -> Optional[str]:
    """
    Détection très simple de divergence RSI sur les ~10 dernières barres.

    Retourne :
      - "bullish"  : prix fait un plus bas plus bas, RSI fait un plus bas plus haut
      - "bearish"  : prix fait un plus haut plus haut, RSI fait un plus haut plus bas
      - None       : pas de divergence claire
    """
    if df is None or _safe_len(df) < 15:
        return None

    close = df["close"].astype(float)
    r = rsi(close)

    p1, p2 = float(close.iloc[-1]), float(close.iloc[-6])
    r1, r2 = float(r.iloc[-1]), float(r.iloc[-6])

    if p1 < p2 and r1 > r2:
        return "bullish"

    if p1 > p2 and r1 < r2:
        return "bearish"

    return None
