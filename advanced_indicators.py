"""
advanced_indicators.py — indicateurs avancés institutionnels
- ADX / DI
- Bollinger Bands
- Keltner Channels
- Squeeze (BB vs KC)
- OBV (On-Balance Volume)
- Historical Volatility percentile (20j)
- EMA Cloud (trend multi-periodes)
"""
import numpy as np
import pandas as pd

def ema(series: pd.Series, span: int) -> pd.Series:
    return series.ewm(span=span, adjust=False).mean()

def true_range(df: pd.DataFrame) -> pd.Series:
    h, l, c = df["high"], df["low"], df["close"]
    tr = pd.concat([(h-l), (h-c.shift()).abs(), (l-c.shift()).abs()], axis=1).max(axis=1)
    return tr

def atr(df: pd.DataFrame, period: int = 14) -> pd.Series:
    return true_range(df).rolling(period).mean()

def adx(df: pd.DataFrame, period: int = 14):
    h, l, c = df["high"], df["low"], df["close"]
    up = h.diff()
    down = -l.diff()
    plus_dm = np.where((up > down) & (up > 0), up, 0.0)
    minus_dm = np.where((down > up) & (down > 0), down, 0.0)
    tr = true_range(df)
    tr_n = tr.rolling(period).sum()
    pdi = 100 * pd.Series(plus_dm).rolling(period).sum() / tr_n
    mdi = 100 * pd.Series(minus_dm).rolling(period).sum() / tr_n
    dx = ( (pdi - mdi).abs() / (pdi + mdi).replace(0, np.nan) ) * 100
    adx_val = dx.rolling(period).mean()
    return pdi.rename("pdi"), mdi.rename("mdi"), adx_val.rename("adx")

def bollinger(close: pd.Series, period: int = 20, mult: float = 2.0):
    ma = close.rolling(period).mean()
    sd = close.rolling(period).std(ddof=0)
    upper = ma + mult * sd
    lower = ma - mult * sd
    return ma, upper, lower

def keltner(df: pd.DataFrame, period: int = 20, mult: float = 1.5):
    mid = ema(df["close"], period)
    rng = atr(df, period)
    upper = mid + mult * rng
    lower = mid - mult * rng
    return mid, upper, lower

def squeeze_on(df: pd.DataFrame, bb_p=20, bb_k=2.0, kc_p=20, kc_k=1.5) -> pd.Series:
    _, bb_u, bb_l = bollinger(df["close"], bb_p, bb_k)
    _, kc_u, kc_l = keltner(df, kc_p, kc_k)
    # squeeze "ON" quand BB est à l'intérieur de KC
    return ((bb_u < kc_u) & (bb_l > kc_l)).astype(int)

def obv(df: pd.DataFrame) -> pd.Series:
    close = df["close"]; vol = df["volume"] if "volume" in df else pd.Series(0, index=close.index)
    direction = np.sign(close.diff().fillna(0))
    return (direction * vol).fillna(0).cumsum()

def hv_percentile(close: pd.Series, lookback: int = 20, window: int = 20) -> float:
    ret = close.pct_change().dropna()
    if len(ret) < lookback + window: 
        return 50.0
    hv = ret.rolling(lookback).std().dropna()
    last = hv.iloc[-1]
    pct = (hv <= last).mean() * 100.0
    return float(pct)

def ema_cloud(close: pd.Series):
    e8 = ema(close, 8); e21 = ema(close, 21); e50 = ema(close, 50); e200 = ema(close, 200)
    return {"ema8": e8, "ema21": e21, "ema50": e50, "ema200": e200}
