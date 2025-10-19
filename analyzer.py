# analyzer.py
from __future__ import annotations
import numpy as np
import pandas as pd

def _ema(s: pd.Series, n: int) -> pd.Series:
    return s.ewm(span=n, adjust=False).mean()

def _atr(df: pd.DataFrame, n=14) -> float:
    h,l,c = df["high"].astype(float), df["low"].astype(float), df["close"].astype(float)
    pc = c.shift(1)
    tr = pd.concat([(h-l).abs(), (h-pc).abs(), (l-pc).abs()], axis=1).max(axis=1)
    a = tr.rolling(n, min_periods=n).mean().iloc[-1]
    return float(a) if pd.notna(a) else 0.0

def _direction_h4(df_h4: pd.DataFrame) -> str:
    c = df_h4["close"].astype(float)
    e20, e50 = _ema(c, 20), _ema(c, 50)
    return "long" if e20.iloc[-1] > e50.iloc[-1] else "short"

def decide(symbol: str, df_h1: pd.DataFrame, df_h4: pd.DataFrame, inst: dict) -> dict:
    side = _direction_h4(df_h4)
    c1 = df_h1["close"].astype(float).iloc[-1]
    a  = max(_atr(df_h1, 14), c1 * 0.003)

    if side == "long":
        entry = c1 * 0.999
        sl    = entry - 1.2*a
        tp1   = entry + 1.2*a
        tp2   = entry + 2.0*a
    else:
        entry = c1 * 1.001
        sl    = entry + 1.2*a
        tp1   = entry - 1.2*a
        tp2   = entry - 2.0*a

    rr = abs((tp2-entry)/(entry-sl))
    valid = (inst.get("score",0) >= 1.2) and rr >= 1.3
    return {
        "valid": valid, "side": side, "entry": float(entry), "sl": float(sl),
        "tp1": float(tp1), "tp2": float(tp2), "rr": float(rr),
        "reason": "OK" if valid else "filters_failed",
        "inst_score": float(inst.get("score",0)),
        "comments": [f"ema20/50 bias={side}", f"atr={a:.8f}"]
    }
