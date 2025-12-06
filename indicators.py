# =====================================================================
# indicators.py — Desk Lead Premium Technical Suite
# Institutionnel, robuste, optimisé Bitget
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


# =====================================================================
# EMA / SMA
# =====================================================================

def ema(series: pd.Series, length: int) -> pd.Series:
    if _safe_len(series) < 2:
        return pd.Series([np.nan] * _safe_len(series))
    return series.ewm(span=length, adjust=False).mean()


def sma(series: pd.Series, length: int) -> pd.Series:
    if _safe_len(series) < length:
        return pd.Series([np.nan] * _safe_len(series))
    return series.rolling(length).mean()


# =====================================================================
# RSI (EMA-Based smoothing, pro)
# =====================================================================

def rsi(series: pd.Series, length: int = 14) -> pd.Series:
    if _safe_len(series) < length + 5:
        return pd.Series([50] * _safe_len(series))

    delta = series.diff()

    gain = delta.clip(lower=0)
    loss = (-delta).clip(lower=0)

    avg_gain = gain.ewm(alpha=1/length, adjust=False).mean()
    avg_loss = loss.ewm(alpha=1/length, adjust=False).mean()

    rs = avg_gain / (avg_loss + 1e-10)
    rsi_val = 100 - (100 / (1 + rs))
    return rsi_val.fillna(50)


# =====================================================================
# MACD
# =====================================================================

def macd(series: pd.Series, fast=12, slow=26, signal=9) -> Dict[str, pd.Series]:
    if _safe_len(series) < slow + signal:
        empty = pd.Series([0] * _safe_len(series))
        return {"macd": empty, "signal": empty, "hist": empty}

    fast_ema = ema(series, fast)
    slow_ema = ema(series, slow)
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
    if _safe_len(df) < length + 2:
        return pd.Series([np.nan] * _safe_len(df))

    high = df["high"]
    low = df["low"]
    prev_close = df["close"].shift(1)

    tr = pd.concat([
        (high - low).abs(),
        (high - prev_close).abs(),
        (low - prev_close).abs(),
    ], axis=1).max(axis=1)

    return tr.ewm(span=length, adjust=False).mean().fillna(method="bfill")


# =====================================================================
# MOMENTUM (simple)
# =====================================================================

def momentum(series: pd.Series, length: int = 10) -> pd.Series:
    return series - series.shift(length)


# =====================================================================
# RSI DIVERGENCE (pro)
# =====================================================================

def detect_rsi_divergence(df: pd.DataFrame) -> Optional[str]:
    if _safe_len(df) < 25:
        return None

    close = df["close"]
    r = rsi(close)

    p1, p2 = close.iloc[-1], close.iloc[-6]
    r1, r2 = r.iloc[-1], r.iloc[-6]

    # Bullish div
    if p1 < p2 and r1 > r2:
        return "BULLISH"

    # Bearish div
    if p1 > p2 and r1 < r2:
        return "BEARISH"

    return None


# =====================================================================
# OTE (62% / 70.5%)
# =====================================================================

def compute_ote(df: pd.DataFrame, bias: str) -> Dict[str, float]:
    lookback = df.tail(30)

    high = lookback["high"].max()
    low = lookback["low"].min()

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
# VOLATILITY REGIME (ATR relative)
# =====================================================================

def volatility_regime(df: pd.DataFrame, length: int = 14) -> str:
    atr = true_atr(df, length)
    close = df["close"]

    atrp = (atr / close).iloc[-1]

    if atrp > 0.03:
        return "EXTREME"
    if atrp > 0.02:
        return "HIGH"
    if atrp < 0.008:
        return "LOW"

    return "NORMAL"


# =====================================================================
# FVG (3-leg ICT)
# =====================================================================

def detect_fvg(df: pd.DataFrame) -> Optional[str]:
    if _safe_len(df) < 5:
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
# Volume spike (institutionnel)
# =====================================================================

def detect_volume_spike(df: pd.DataFrame, factor: float = 2.2) -> bool:
    vol = df["volume"]
    if _safe_len(vol) < 25:
        return False

    avg = vol.rolling(20).mean().iloc[-1]
    return vol.iloc[-1] > factor * avg


# =====================================================================
# INSTITUTIONAL MOMENTUM (PRO)
# Combinaison :
#   - MACD hist slope
#   - EMA20/50 trend
#   - RSI confirmation
#   - Volume anomalies
# =====================================================================

def institutional_momentum(df: pd.DataFrame) -> str:
    close = df["close"]

    mac = macd(close)
    hist = mac["hist"]

    r = rsi(close)
    ema20 = ema(close, 20)
    ema50 = ema(close, 50)

    # MACD slope = accélération court terme institutionnelle
    macd_slope = hist.iloc[-1] - hist.iloc[-4]

    vol_spike = detect_volume_spike(df)

    bullish = (
        hist.iloc[-1] > 0
        and macd_slope > 0
        and ema20.iloc[-1] > ema50.iloc[-1]
        and r.iloc[-1] > 50
        and not vol_spike
    )

    bearish = (
        hist.iloc[-1] < 0
        and macd_slope < 0
        and ema20.iloc[-1] < ema50.iloc[-1]
        and r.iloc[-1] < 50
        and not vol_spike
    )

    if bullish:
        return "STRONG_BULLISH" if macd_slope > 0 else "BULLISH"

    if bearish:
        return "STRONG_BEARISH" if macd_slope < 0 else "BEARISH"

    return "NEUTRAL"
