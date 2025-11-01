# liq_proxy.py — proxy très simple d’activité liquidation-like
import pandas as pd
import numpy as np

def liquidation_spike_proxy(df: pd.DataFrame, lookback:int=48) -> float:
    """
    Proxy 0..1: mèche/true range et volume extrêmes récentes → probables zones de liquidation balayées.
    """
    h,l,c,v = df["high"].astype(float), df["low"].astype(float), df["close"].astype(float), df.get("volume", pd.Series([0]*len(df))).astype(float)
    tr = (h - l).rolling(lookback).mean()
    wick = (h - c).abs().add((c - l).abs())  # somme des mèches
    s = 0.5*(wick/ (tr.replace(0,np.nan))).fillna(0) + 0.5*(v / (v.rolling(lookback).mean().replace(0,np.nan))).fillna(0)
    x = float(s.iloc[-1])
    # squash 0..1
    return float(np.tanh(x/5.0))
