# =====================================================================
# indicators.py — outils techniques institutionnels
# =====================================================================
import numpy as np
import pandas as pd
from typing import Dict, Any, Optional, Tuple


# -------------------------------------------------------------
# EMA / SMA
# -------------------------------------------------------------
def ema(series: pd.Series, length: int) -> pd.Series:
    return series.ewm(span=length, adjust=False).mean()


def sma(series: pd.Series, length: int) -> pd.Series:
    return series.rolling(length).mean()


# -------------------------------------------------------------
# RSI
# -------------------------------------------------------------
def rsi(series: pd.Series, length: int = 14) -> pd.Series:
    delta = series.diff()
    gain = np.where(delta > 0, delta, 0)
    loss = np.where(delta < 0, -delta, 0)

    gain_ema = pd.Series(gain).ewm(span=length, adjust=False).mean()
    loss_ema = pd.Series(loss).ewm(span=length, adjust=False).mean()

    rs = gain_ema / (loss_ema + 1e-12)
    return 100 - (100 / (1 + rs))


# -------------------------------------------------------------
# MACD
# -------------------------------------------------------------
def macd(series: pd.Series, fast=12, slow=26, signal=9) -> Dict[str, pd.Series]:
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


# -------------------------------------------------------------
# ATR (vrai)
# -------------------------------------------------------------
def true_atr(df: pd.DataFrame, length: int = 14) -> pd.Series:
    high = df["high"]
    low = df["low"]
    close = df["close"]
    prev_close = close.shift(1)

    tr = pd.concat(
        [
            (high - low).abs(),
            (high - prev_close).abs(),
            (low - prev_close).abs(),
        ],
        axis=1,
    ).max(axis=1)

    return tr.rolling(length).mean()


# -------------------------------------------------------------
# Momentum simple
# -------------------------------------------------------------
def momentum(series: pd.Series, length: int = 10) -> pd.Series:
    return series - series.shift(length)


# -------------------------------------------------------------
# Divergence RSI
# -------------------------------------------------------------
def detect_rsi_divergence(df: pd.DataFrame) -> Optional[str]:
    """
    Divergence classique :
        - Bullish : prix ↓ mais RSI ↑
        - Bearish : prix ↑ mais RSI ↓
    """
    r = rsi(df["close"], 14)
    if len(r) < 20:
        return None

    close = df["close"]

    # Lookback court
    p1 = close.iloc[-1]
    p2 = close.iloc[-5]
    r1 = r.iloc[-1]
    r2 = r.iloc[-5]

    if p1 < p2 and r1 > r2:
        return "BULLISH"
    if p1 > p2 and r1 < r2:
        return "BEARISH"
    return None


# -------------------------------------------------------------
# OTE (Optimal Trade Entry)
# -------------------------------------------------------------
def compute_ote(df: pd.DataFrame, bias: str) -> Dict[str, float]:
    """
    Retourne les niveaux OTE 62% / 705% :
        - Biais LONG → swing low vers swing high
        - Biais SHORT → swing high vers swing low
    """
    if len(df) < 10:
        return {"ote_62": None, "ote_705": None}

    high = df["high"].iloc[-10:].max()
    low = df["low"].iloc[-10:].min()

    if bias.upper() == "LONG":
        ote_62 = low + (high - low) * 0.62
        ote_705 = low + (high - low) * 0.705
    else:
        ote_62 = high - (high - low) * 0.62
        ote_705 = high - (high - low) * 0.705

    return {"ote_62": ote_62, "ote_705": ote_705}


# -------------------------------------------------------------
# Volatility regime (simple ATR%)
# -------------------------------------------------------------
def volatility_regime(df: pd.DataFrame, length: int = 14) -> str:
    atr = true_atr(df, length)
    close = df["close"]
    atrp = atr / close

    if atrp.iloc[-1] > 0.02:   # très volatile
        return "HIGH"
    if atrp.iloc[-1] < 0.008:  # faible
        return "LOW"
    return "NORMAL"


# -------------------------------------------------------------
# Fair Value Gap (FVG)
# -------------------------------------------------------------
def detect_fvg(df: pd.DataFrame) -> Optional[str]:
    """
    Simple FVG 3-candles :
        - Bullish FVG → low[n] > high[n-2]
        - Bearish FVG → high[n] < low[n-2]
    """
    if len(df) < 5:
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


# -------------------------------------------------------------
# Volume anomalies (simplifié)
# -------------------------------------------------------------
def detect_volume_spike(df: pd.DataFrame, factor: float = 2.0) -> bool:
    """
    Spike de volume simple :
        volume[n] > factor * moyenne(20)
    """
    if "volume" not in df.columns:
        return False

    vol = df["volume"]
    if len(vol) < 21:
        return False

    if vol.iloc[-1] > factor * vol.rolling(20).mean().iloc[-1]:
        return True

    return False


# -------------------------------------------------------------
# Momentum institutionnel simple (MACD + RSI + vol)
# -------------------------------------------------------------
def institutional_momentum(df: pd.DataFrame) -> str:
    mac = macd(df["close"])
    r = rsi(df["close"])
    vol_spike = detect_volume_spike(df)

    bullish = (
        mac["hist"].iloc[-1] > 0 and
        r.iloc[-1] > 50 and
        not vol_spike
    )

    bearish = (
        mac["hist"].iloc[-1] < 0 and
        r.iloc[-1] < 50 and
        not vol_spike
    )

    if bullish:
        return "BULLISH"
    if bearish:
        return "BEARISH"
    return "NEUTRAL"
