# stops.py — stops structure + ATR + buffers
from __future__ import annotations
import numpy as np
from indicators_true_atr import atr_wilder
from settings import ATR_LEN, ATR_MULT_SL, STRUCT_LOOKBACK, SL_BUFFER_PCT, SL_BUFFER_TICKS

def _round_to_tick(x: float, tick: float) -> float:
    if tick <= 0: return float(x)
    steps = int(float(x)/tick)
    return round(steps * tick, 8)

def protective_stop_long(df, entry: float, tick: float) -> float:
    atr = float(atr_wilder(df, ATR_LEN).iloc[-1])
    swing = float(df["low"].rolling(STRUCT_LOOKBACK).min().iloc[-2])  # évite la barre en cours
    sl_atr = entry - ATR_MULT_SL * atr
    raw = min(swing, sl_atr)
    raw *= (1 - SL_BUFFER_PCT)
    sl = _round_to_tick(raw, tick) - SL_BUFFER_TICKS * tick
    return max(1e-8, sl)

def protective_stop_short(df, entry: float, tick: float) -> float:
    atr = float(atr_wilder(df, ATR_LEN).iloc[-1])
    swing = float(df["high"].rolling(STRUCT_LOOKBACK).max().iloc[-2])
    sl_atr = entry + ATR_MULT_SL * atr
    raw = max(swing, sl_atr)
    raw *= (1 + SL_BUFFER_PCT)
    sl = _round_to_tick(raw, tick) + SL_BUFFER_TICKS * tick
    return max(1e-8, sl)
