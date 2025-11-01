# indicators_true_atr.py — ATR "vrai" (Wilder)
import pandas as pd
import numpy as np

def true_range(df: pd.DataFrame) -> pd.Series:
    prev_close = df["close"].shift(1)
    tr = pd.concat([
        df["high"] - df["low"],
        (df["high"] - prev_close).abs(),
        (df["low"] - prev_close).abs()
    ], axis=1).max(axis=1)
    return tr

def atr_wilder(df: pd.DataFrame, length: int = 14) -> pd.Series:
    tr = true_range(df)
    atr = tr.ewm(alpha=1/length, adjust=False).mean()
    return atr
