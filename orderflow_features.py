import pandas as pd
import numpy as np

def compute_atr(df: pd.DataFrame, n=14):
    h,l,c = df["high"], df["low"], df["close"]
    pc=c.shift(1)
    tr = pd.concat([(h-l).abs(), (h-pc).abs(), (l-pc).abs()], axis=1).max(axis=1)
    return tr.rolling(n).mean()

def ema(s: pd.Series, n: int): return s.ewm(span=n, adjust=False).mean()

def equal_highs_lows(df: pd.DataFrame, lookback=120, precision=2):
    w=df.tail(lookback)
    highs=w["high"].round(precision).value_counts()
    lows =w["low"].round(precision).value_counts()
    return any(c>=3 for c in highs.values), any(c>=3 for c in lows.values)

def volume_profile_nodes(df: pd.DataFrame, bins=24):
    prices=df["close"].values; vols=df["volume"].values
    if len(prices)<10: return {"hvn":[], "lvn":[]}
    hist,edges=np.histogram(prices, bins=bins, weights=vols)
    centers=(edges[:-1]+edges[1:])/2
    idx=np.argsort(hist)
    hvn=[(float(centers[i]), float(hist[i])) for i in sorted(idx[-3:])]
    lvn=[(float(centers[i]), float(hist[i])) for i in sorted(idx[:3])]
    return {"hvn":hvn, "lvn":lvn}
