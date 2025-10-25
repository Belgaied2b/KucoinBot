# indicators.py
from __future__ import annotations
import numpy as np
import pandas as pd

# =======================
#   Indicateurs existants
# =======================

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

def compute_atr(df: pd.DataFrame, period: int = 14) -> pd.Series:
    hl = (df["high"] - df["low"]).abs()
    hc = (df["high"] - df["close"].shift()).abs()
    lc = (df["low"] - df["close"].shift()).abs()
    tr = pd.concat([hl, hc, lc], axis=1).max(axis=1)
    return tr.rolling(period).mean()

def is_momentum_ok(close: pd.Series, vol: pd.Series) -> bool:
    macd, sig, hist = compute_macd(close)
    ema20, ema50 = compute_ema(close, 20), compute_ema(close, 50)
    mom = bool(hist.iloc[-1] > 0 and ema20.iloc[-1] > ema50.iloc[-1])
    vol_ok = bool(vol.iloc[-1] > vol.rolling(20).mean().iloc[-1])
    return mom and vol_ok

# =======================
#   Ajouts institutionnels
# =======================

def compute_vwap(df: pd.DataFrame) -> pd.Series:
    """
    VWAP cumulatif (classique) sur tout le DataFrame.
    Requiert colonnes: ['high','low','close','volume'].
    """
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
    """
    VWAP par session (réinitialisé à chaque période):
      - 'D' = journalier
      - 'W' = hebdomadaire
      - 'M' = mensuel
    Requiert une colonne 'timestamp' en millisecondes ou datetimes.
    """
    if df is None or df.empty:
        return pd.Series(dtype=float, name=f"vwap_{session_key}")
    # Convertit timestamp -> période
    ts = pd.to_datetime(df.get("timestamp", pd.NaT), unit="ms", errors="coerce")
    if ts.isna().all():
        # Si pas de timestamp exploitable, fallback sur VWAP cumulatif
        return compute_vwap(df).rename(f"vwap_{session_key}")

    tp = (df["high"] + df["low"] + df["close"]) / 3.0
    vol = df["volume"].astype(float).replace(0, np.nan)
    period = ts.dt.to_period(session_key)

    # cumuls par période
    num = (tp * vol).groupby(period).cumsum()
    den = vol.groupby(period).cumsum()
    vwap = num / den
    vwap.index = df.index
    vwap.name = f"vwap_{session_key}"
    return vwap

def is_vwap_location_ok(
    df: pd.DataFrame,
    idx: int = -1,
    mode: str = "above_session",
    session_key: str = "D"
) -> bool:
    """
    True si le prix est du bon côté du VWAP de session.
    mode:
      - 'above_session' → prix > VWAP(session) (utile pour LONG)
      - 'below_session' → prix < VWAP(session) (utile pour SHORT)
      - 'any'           → pas de filtre
    """
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

def is_ema_trend_ok(df: pd.DataFrame, bias: str = "LONG") -> bool:
    """
    Filtre de tendance simple et robuste:
      - LONG : close > EMA50 et EMA20 > EMA50
      - SHORT: close < EMA50 et EMA20 < EMA50
    """
    if df is None or len(df) < 60:
        return True
    close = df["close"]
    ema20 = compute_ema(close, 20)
    ema50 = compute_ema(close, 50)
    if bias.upper() == "LONG":
        return bool(close.iloc[-1] > ema50.iloc[-1] and ema20.iloc[-1] > ema50.iloc[-1])
    else:
        return bool(close.iloc[-1] < ema50.iloc[-1] and ema20.iloc[-1] < ema50.iloc[-1])
