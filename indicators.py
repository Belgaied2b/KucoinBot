import pandas as pd, numpy as np

def compute_rsi(series: pd.Series, period: int = 14) -> pd.Series:
    delta = series.diff()
    gain = delta.clip(lower=0).ewm(alpha=1/period, min_periods=period).mean()
    loss = -delta.clip(upper=0).ewm(alpha=1/period, min_periods=period).mean()
    rs = gain / (loss.replace(0, np.nan))
    rsi = 100 - (100/(1+rs))
    return rsi.fillna(method="bfill")

def compute_macd(series: pd.Series, fast=12, slow=26, signal=9):
    ema_fast = series.ewm(span=fast).mean()
    ema_slow = series.ewm(span=slow).mean()
    macd = ema_fast - ema_slow
    signal_line = macd.ewm(span=signal).mean()
    hist = macd - signal_line
    return macd, signal_line, hist

def compute_ema(series: pd.Series, period=20):
    return series.ewm(span=period).mean()

def compute_atr(df: pd.DataFrame, period=14):
    hl = df["high"] - df["low"]
    hc = (df["high"] - df["close"].shift()).abs()
    lc = (df["low"] - df["close"].shift()).abs()
    tr = pd.concat([hl, hc, lc], axis=1).max(axis=1)
    return tr.rolling(period).mean()

def is_momentum_ok(close: pd.Series, vol: pd.Series) -> bool:
    macd, sig, hist = compute_macd(close)
    ema20, ema50 = compute_ema(close,20), compute_ema(close,50)
    mom = hist.iloc[-1] > 0 and ema20.iloc[-1] > ema50.iloc[-1]
    vol_ok = vol.iloc[-1] > vol.rolling(20).mean().iloc[-1]
    return bool(mom and vol_ok)
