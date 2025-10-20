"""
structure_utils.py
Détection de structures de marché : BOS, CHoCH, COS + patterns de chandeliers.
"""
import pandas as pd

def detect_bos(df: pd.DataFrame, lookback=10):
    highs = df["high"].rolling(lookback).max()
    lows = df["low"].rolling(lookback).min()
    if df["close"].iloc[-1] > highs.iloc[-2]:
        return "BOS_UP"
    elif df["close"].iloc[-1] < lows.iloc[-2]:
        return "BOS_DOWN"
    return None

def detect_choch(df: pd.DataFrame, lookback=10):
    bos = detect_bos(df, lookback)
    if bos == "BOS_UP" and df["close"].iloc[-1] < df["low"].iloc[-2]:
        return "CHoCH_DOWN"
    elif bos == "BOS_DOWN" and df["close"].iloc[-1] > df["high"].iloc[-2]:
        return "CHoCH_UP"
    return None
