"""
indicators.py
Tous les indicateurs techniques du bot institutionnel.
"""
import pandas as pd
import numpy as np

def compute_rsi(series: pd.Series, period: int = 14) -> pd.Series:
    delta = series.diff()
    gain = delta.clip(lower=0).ewm(alpha=1/period, min_periods=period).mean()
    loss = -delta.clip(upper=0).ewm(alpha=1/period, min_periods=period).mean()
    rs = gain / loss
    return 100 - (100 / (1 + rs))

def compute_macd(series: pd.Series, fast=12, slow=26, signal=9):
    ema_fast = series.ewm(span=fast).mean()
    ema_slow = series.ewm(span=slow).mean()
    macd = ema_fast - ema_slow
    signal_line = macd.ewm(span=signal).mean()
    return macd, signal_line

def compute_ema(series: pd.Series, period=20):
    return series.ewm(span=period).mean()

def compute_atr(df: pd.DataFrame, period=14):
    high_low = df["high"] - df["low"]
    high_close = np.abs(df["high"] - df["close"].shift())
    low_close = np.abs(df["low"] - df["close"].shift())
    tr = high_low.combine(high_close, np.maximum).combine(low_close, np.maximum)
    return tr.rolling(period).mean()

def is_bullish_engulfing(df):
    prev = df.iloc[-2]
    last = df.iloc[-1]
    return prev["close"] < prev["open"] and last["close"] > last["open"] and last["close"] > prev["open"] and last["open"] < prev["close"]

def is_bearish_engulfing(df):
    prev = df.iloc[-2]
    last = df.iloc[-1]
    return prev["close"] > prev["open"] and last["close"] < last["open"] and last["close"] < prev["open"] and last["open"] > prev["close"]
