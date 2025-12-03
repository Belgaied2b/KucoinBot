# ============================================================
# indicators.py — VERSION DESK LEAD
# Indicateurs techniques + institutionnels robustes
# ============================================================

from __future__ import annotations
import numpy as np
import pandas as pd

# ============================================================
# RSI / MACD / EMA
# ============================================================

def compute_rsi(series: pd.Series, period: int = 14) -> pd.Series:
    delta = series.diff()
    gain = delta.clip(lower=0).ewm(alpha=1/period, min_periods=period).mean()
    loss = -delta.clip(upper=0).ewm(alpha=1/period, min_periods=period).mean()
    rs = gain / (loss.replace(0, np.nan))
    rsi = 100 - (100 / (1 + rs))
    return rsi.fillna(method="bfill")


def compute_macd(series: pd.Series, fast: int = 12, slow: int = 26, signal: int = 9):
    ema_fast = series.ewm(span=fast, adjust=False).mean()
    ema_slow = series.ewm(span=slow, adjust=False).mean()
    macd = ema_fast - ema_slow
    signal_line = macd.ewm(span=signal, adjust=False).mean()
    hist = macd - signal_line
    return macd, signal_line, hist


def compute_ema(series: pd.Series, period: int = 20):
    return series.ewm(span=period, adjust=False).mean()


# ============================================================
# ATR — version classique (True ATR intégré dans indicators_true_atr)
# ============================================================

def compute_atr(df: pd.DataFrame, period: int = 14) -> pd.Series:
    hl = (df["high"] - df["low"]).abs()
    hc = (df["high"] - df["close"].shift()).abs()
    lc = (df["low"] - df["close"].shift()).abs()
    tr = pd.concat([hl, hc, lc], axis=1).max(axis=1)
    return tr.rolling(period).mean()


# ============================================================
# Momentum simple existant
# ============================================================

def is_momentum_ok(close: pd.Series, vol: pd.Series) -> bool:
    macd, sig, hist = compute_macd(close)
    ema20, ema50 = compute_ema(close, 20), compute_ema(close, 50)
    mom = bool(hist.iloc[-1] > 0 and ema20.iloc[-1] > ema50.iloc[-1])
    vol_ok = bool(vol.iloc[-1] > vol.rolling(20).mean().iloc[-1])
    return mom and vol_ok


# ============================================================
# VWAP — Global + Session
# ============================================================

def compute_vwap(df: pd.DataFrame) -> pd.Series:
    if df is None or df.empty:
        return pd.Series(dtype=float, name="vwap")
    tp = (df["high"] + df["low"] + df["close"]) / 3.0
    vol = df["volume"].astype(float).replace(0, np.nan)
    num = (tp * vol).cumsum()
    den = vol.cumsum()
    vwap = num / den
    vwap.name = "vwap"
    return vwap


def compute_session_vwap(df: pd.DataFrame, session_key: str = "D") -> pd.Series:
    if df is None or df.empty:
        return pd.Series(dtype=float, name=f"vwap_{session_key}")

    ts = pd.to_datetime(df.get("timestamp", pd.NaT), unit="ms", errors="coerce")
    if ts.isna().all():
        return compute_vwap(df).rename(f"vwap_{session_key}")

    tp = (df["high"] + df["low"] + df["close"]) / 3.0
    vol = df["volume"].astype(float).replace(0, np.nan)
    period = ts.dt.to_period(session_key)
    num = (tp * vol).groupby(period).cumsum()
    den = vol.groupby(period).cumsum()
    vwap = num / den
    vwap.index = df.index
    vwap.name = f"vwap_{session_key}"
    return vwap


def is_vwap_location_ok(df: pd.DataFrame, idx: int = -1, mode: str = "above_session", session_key: str = "D") -> bool:
    if df is None or len(df) < 5 or mode == "any":
        return True
    vw = compute_session_vwap(df, session_key=session_key)
    try:
        price = float(df["close"].iloc[idx])
        v = float(vw.iloc[idx])
    except Exception:
        return True
    if mode == "above_session":
        return price > v
    if mode == "below_session":
        return price < v
    return True


# ============================================================
# EMA Trend (HTF or LTF)
# ============================================================

def is_ema_trend_ok(df: pd.DataFrame, bias: str = "LONG") -> bool:
    if df is None or len(df) < 60:
        return True
    close = df["close"]
    ema20 = compute_ema(close, 20)
    ema50 = compute_ema(close, 50)
    if bias.upper() == "LONG":
        return bool(close.iloc[-1] > ema50.iloc[-1] and ema20.iloc[-1] > ema50.iloc[-1])
    return bool(close.iloc[-1] < ema50.iloc[-1] and ema20.iloc[-1] < ema50.iloc[-1])


# ============================================================
# INSTITUTIONNEL — CVD / Delta Volume (Fallback)
# ============================================================

def compute_delta_volume(df: pd.DataFrame) -> pd.Series:
    """
    Approximation CVD-like:
      CVD = Σ( (close - open) * volume )
    """
    try:
        delta = (df["close"] - df["open"]) * df["volume"]
        cvd = delta.cumsum()
        return cvd.rename("cvd")
    except Exception:
        return pd.Series(np.zeros(len(df)), index=df.index, name="cvd")


# ============================================================
# INSTITUTIONNEL — Momentum Score Pro
# ============================================================

def institutional_momentum_score(close: pd.Series, cvd: pd.Series, lookback: int = 50) -> float:
    """
    Score 0..1 basé sur :
      - pente MACD
      - force EMA20/50
      - cohérence CVD
    """
    if len(close) < 60:
        return 0.5

    macd, sig, hist = compute_macd(close)
    ema20, ema50 = compute_ema(close, 20), compute_ema(close, 50)

    macd_slope = float(hist.diff().iloc[-1])
    ema_trend = (ema20.iloc[-1] - ema50.iloc[-1]) / max(abs(close.iloc[-1]), 1e-6)

    cvd = pd.Series(cvd).dropna()
    if len(cvd) < 5:
        cvd_slope = 0
    else:
        cvd_slope = float((cvd.iloc[-1] - cvd.iloc[-5]) / max(abs(cvd.iloc[-5]), 1e-6))

    raw = (0.4 * macd_slope + 0.4 * ema_trend + 0.2 * cvd_slope)
    score = 1 / (1 + np.exp(-4 * raw))  # logistic transform
    return float(np.clip(score, 0, 1))


# ============================================================
# VOLATILITY REGIME — Compression / Expansion
# ============================================================

def volatility_regime(df: pd.DataFrame, period: int = 20) -> str:
    """
    Retourne :
      - "compression" si ATR < EMA(ATR)
      - "expansion"   si ATR > EMA(ATR)
      - "unknown"
    """
    if df is None or len(df) < 50:
        return "unknown"

    atr = compute_atr(df, period)
    ema_atr = compute_ema(atr, period)

    if atr.iloc[-1] < ema_atr.iloc[-1]:
        return "compression"
    if atr.iloc[-1] > ema_atr.iloc[-1]:
        return "expansion"
    return "unknown"


# ============================================================
# PREMIUM / DISCOUNT ZONES (confluence structure-utils)
# ============================================================

def compute_premium_discount(df: pd.DataFrame, lookback: int = 50):
    if df is None or len(df) < lookback:
        return False, False
    sub = df.tail(lookback)
    hi = float(sub["high"].max())
    lo = float(sub["low"].min())
    mid = 0.5 * (hi + lo)
    close = float(df["close"].iloc[-1])
    return close < mid, close > mid  # discount, premium


# ============================================================
# MULTI-TIMEFRAME MOMENTUM (H1/H4)
# ============================================================

def multi_tf_momentum(close_h1: pd.Series, close_h4: pd.Series) -> str:
    """
    Combine H1 + H4 momentum :
      - "aligned_up"
      - "aligned_down"
      - "divergent"
    """
    macd1, _, h1 = compute_macd(close_h1)
    macd4, _, h4 = compute_macd(close_h4)

    last1 = h1.iloc[-1]
    last4 = h4.iloc[-1]

    if last1 > 0 and last4 > 0:
        return "aligned_up"
    if last1 < 0 and last4 < 0:
        return "aligned_down"
    return "divergent"


# ============================================================
# VOLUME AGGRESSIF (SANS API EXTERNE)
# ============================================================

def is_aggressive_volume_ok(df: pd.DataFrame) -> bool:
    """
    Proxy delta volume agressif :
      - hausse de volume AND bougie directionnelle
    """
    if len(df) < 20:
        return True

    vol = df["volume"].iloc[-1]
    avg = df["volume"].rolling(20).mean().iloc[-1]
    body = df["close"].iloc[-1] - df["open"].iloc[-1]

    return bool(vol > avg and abs(body) > (df["high"].iloc[-1] - df["low"].iloc[-1]) * 0.35)


# ============================================================
# FLAG DE MOMENTUM STRUCTUREL COMPLET (Desk Lead)
# ============================================================

def structural_momentum_flag(df: pd.DataFrame) -> str:
    """
    Donne une lecture simple :
      - "bullish"
      - "bearish"
      - "neutral"
    Basé sur EMA20/50 + MACD histogram.
    """
    if len(df) < 60:
        return "neutral"

    close = df["close"]
    ema20 = compute_ema(close, 20)
    ema50 = compute_ema(close, 50)
    _, _, hist = compute_macd(close)

    if ema20.iloc[-1] > ema50.iloc[-1] and hist.iloc[-1] > 0:
        return "bullish"
    if ema20.iloc[-1] < ema50.iloc[-1] and hist.iloc[-1] < 0:
        return "bearish"
    return "neutral"
