import pandas as pd

def detect_bos(df: pd.DataFrame, lookback=10):
    highs = df["high"].rolling(lookback).max()
    lows = df["low"].rolling(lookback).min()
    if df["close"].iloc[-1] > (highs.iloc[-2] if len(df)>2 else df["high"].iloc[-2]):
        return "BOS_UP"
    if df["close"].iloc[-1] < (lows.iloc[-2] if len(df)>2 else df["low"].iloc[-2]):
        return "BOS_DOWN"
    return None

def structure_valid(df: pd.DataFrame, bias: str, lookback=10) -> bool:
    bos = detect_bos(df, lookback)
    return (bias=="LONG" and bos=="BOS_UP") or (bias=="SHORT" and bos=="BOS_DOWN")
