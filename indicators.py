# =====================================================================
# indicators.py — Desk Lead Premium Technical Suite
# Institutionnel, robuste, optimisé Bitget / Binance
# =====================================================================

import numpy as np
import pandas as pd
from typing import Dict, Any, Optional


# =====================================================================
# SAFE HELPERS
# =====================================================================

def _safe_len(obj) -> int:
    try:
        return len(obj)
    except:
        return 0


def _safe_series(series, fill=0.0):
    if series is None:
        return pd.Series([fill])
    return pd.Series(series)


# =====================================================================
# EMA / SMA
# =====================================================================

def ema(series: pd.Series, length: int) -> pd.Series:
    s = _safe_series(series)
    if _safe_len(s) < 2:
        return pd.Series([np.nan] * _safe_len(s))
    return s.ewm(span=length, adjust=False).mean()


def sma(series: pd.Series, length: int) -> pd.Series:
    s = _safe_series(series)
    if _safe_len(s) < length:
        return pd.Series([np.nan] * _safe_len(s))
    return s.rolling(length).mean()


# =====================================================================
# RSI — EMA-based (ICT style)
# =====================================================================

def rsi(series: pd.Series, length: int = 14) -> pd.Series:
    s = _safe_series(series)
    if _safe_len(s) < length + 5:
        return pd.Series([50] * _safe_len(s))

    delta = s.diff()

    gain = delta.clip(lower=0)
    loss = (-delta).clip(lower=0)

    avg_gain = gain.ewm(alpha=1/length, adjust=False).mean()
    avg_loss = loss.ewm(alpha=1/length, adjust=False).mean()

    rs = avg_gain / (avg_loss + 1e-10)
    rsi_val = 100 - (100 / (1 + rs))

    return rsi_val.fillna(50)


# =====================================================================
# MACD (pro, stable)
# =====================================================================

def macd(series: pd.Series, fast=12, slow=26, signal=9) -> Dict[str, pd.Series]:
    s = _safe_series(series)

    if _safe_len(s) < slow + signal + 5:
        empty = pd.Series([0] * _safe_len(s))
        return {"macd": empty, "signal": empty, "hist": empty}

    fast_ema = ema(s, fast)
    slow_ema = ema(s, slow)

    macd_line = fast_ema - slow_ema
    signal_line = ema(macd_line, signal)
    hist = macd_line - signal_line

    return {
        "macd": macd_line,
        "signal": signal_line,
        "hist": hist,
    }


# =====================================================================
# TRUE ATR (institutionnel)
# =====================================================================

def true_atr(df: pd.DataFrame, length: int = 14) -> pd.Series:
    if df is None or _safe_len(df) < length + 3:
        return pd.Series([np.nan] * _safe_len(df))

    high = df["high"]
    low = df["low"]
    close = df["close"]
    prev_close = close.shift(1)

    tr = pd.concat([
        (high - low).abs(),
        (high - prev_close).abs(),
        (low - prev_close).abs(),
    ], axis=1).max(axis=1)

    return tr.ewm(span=length, adjust=False).mean().fillna(method="bfill")


# =====================================================================
# MOMENTUM SIMPLE
# =====================================================================

def momentum(series: pd.Series, length: int = 10) -> pd.Series:
    return _safe_series(series) - _safe_series(series).shift(length)


# =====================================================================
# RSI DIVERGENCE
# =====================================================================

def detect_rsi_divergence(df: pd.DataFrame) -> Optional[str]:
    if df is None or _safe_len(df) < 25:
        return None

    close = df["close"]
    r = rsi(close)

    p1, p2 = close.iloc[-1], close.iloc[-6]
    r1, r2 = r.iloc[-1], r.iloc[-6]

    if p1 < p2 and r1 > r2:
        return "BULLISH"
    if p1 > p2 and r1 < r2:
        return "BEARISH"

    return None


# =====================================================================
# OTE 62% / 70.5%
# =====================================================================

def compute_ote(df: pd.DataFrame, bias: str) -> Dict[str, float]:
    if df is None or _safe_len(df) < 10:
        return {"ote_62": None, "ote_705": None}

    sub = df.tail(30)
    high = sub["high"].max()
    low = sub["low"].min()

    if bias.upper() == "LONG":
        return {
            "ote_62": low + (high - low) * 0.62,
            "ote_705": low + (high - low) * 0.705,
        }

    return {
        "ote_62": high - (high - low) * 0.62,
        "ote_705": high - (high - low) * 0.705,
    }


# =====================================================================
# VOLATILITY REGIME
# =====================================================================

def volatility_regime(df: pd.DataFrame, length: int = 14) -> str:
    atr = true_atr(df, length)
    close = df["close"]

    val = float((atr / close).iloc[-1])

    if val > 0.03:
        return "EXTREME"
    if val > 0.02:
        return "HIGH"
    if val < 0.008:
        return "LOW"
    return "NORMAL"


# =====================================================================
# FVG (ICT 3-leg)
# =====================================================================

def detect_fvg(df: pd.DataFrame) -> Optional[str]:
    if df is None or _safe_len(df) < 5:
        return None

    h2 = df["high"].iloc[-3]
    l2 = df["low"].iloc[-3]

    h0 = df["high"].iloc[-1]
    l0 = df["low"].iloc[-1]

    if l0 > h2:
        return "BULLISH_FVG"
    if h0 < l2:
        return "BEARISH_FVG"

    return None


# =====================================================================
# VOLUME SPIKE (institutionnel)
# =====================================================================

def detect_volume_spike(df: pd.DataFrame, factor: float = 2.2) -> bool:
    if df is None or "volume" not in df.columns:
        return False

    vol = df["volume"]
    if _safe_len(vol) < 25:
        return False

    avg = vol.rolling(20).mean().iloc[-1]
    return vol.iloc[-1] > factor * avg


# =====================================================================
# INSTITUTIONAL MOMENTUM (PRO)
# Combination:
#   - MACD slope
#   - EMA20/50 spread
#   - RSI confirmation
#   - No negative volume anomaly
# =====================================================================

def institutional_momentum(df: pd.DataFrame) -> str:
    if df is None or _safe_len(df) < 60:
        return "NEUTRAL"

    close = df["close"]

    mac = macd(close)
    hist = mac["hist"]

    r = rsi(close)
    ema20 = ema(close, 20)
    ema50 = ema(close, 50)

    macd_slope = hist.iloc[-1] - hist.iloc[-4]
    vol_spike = detect_volume_spike(df)

    if (
        hist.iloc[-1] > 0
        and macd_slope > 0
        and ema20.iloc[-1] > ema50.iloc[-1]
        and r.iloc[-1] > 50
        and not vol_spike
    ):
        return "STRONG_BULLISH" if macd_slope > 0 else "BULLISH"

    if (
        hist.iloc[-1] < 0
        and macd_slope < 0
        and ema20.iloc[-1] < ema50.iloc[-1]
        and r.iloc[-1] < 50
        and not vol_spike
    ):
        return "STRONG_BEARISH" if macd_slope < 0 else "BEARISH"

    return "NEUTRAL"
