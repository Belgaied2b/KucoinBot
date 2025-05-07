# signal_analysis.py

import pandas as pd
import numpy as np
from indicators import compute_rsi, compute_macd, compute_atr

def detect_fvg(df: pd.DataFrame) -> bool:
    """
    Renvoie True si on trouve au moins un Fair Value Gap haussier
    (High[i-2] < Low[i]) dans les dernières bougies.
    """
    lows  = df['low'].values
    highs = df['high'].values
    for i in range(2, len(df)):
        if highs[i-2] < lows[i]:
            return True
    return False

def detect_fvg_short(df: pd.DataFrame) -> bool:
    """
    Renvoie True si on trouve au moins un Fair Value Gap baissier
    (Low[i-2] > High[i]) dans les dernières bougies.
    """
    lows  = df['low'].values
    highs = df['high'].values
    for i in range(2, len(df)):
        if lows[i-2] > highs[i]:
            return True
    return False

def analyze_market(symbol: str, df: pd.DataFrame, side: str = "long"):
    """
    Détecte une configuration long ou short selon `side`.
    Retourne un dict avec keys :
      entry_min, entry_max, entry_price, stop_loss, tp1, tp2
    ou None si pas de signal.
    """
    # --- 1) Swing high/low sur les N dernières bougies ---
    window = 20
    if len(df) < window:
        return None
    swing_high = df['high'].rolling(window).max().iloc[-2]
    swing_low  = df['low'].rolling(window).min().iloc[-2]

    # --- 2) Bornes Fibonacci adaptées ---
    if side == "long":
        fib_min = swing_low  + 0.618 * (swing_high - swing_low)
        fib_max = swing_low  + 0.786 * (swing_high - swing_low)
    else:  # short
        fib_max = swing_high - 0.618 * (swing_high - swing_low)
        fib_min = swing_high - 0.786 * (swing_high - swing_low)

    entry_min, entry_max = fib_min, fib_max
    last_price = df['close'].iloc[-1]

    # --- 3) Filtre de tendance : MM50 vs MM200 ---
    ma50  = df['close'].rolling(50).mean().iloc[-1]
    ma200 = df['close'].rolling(200).mean().iloc[-1]
    if side == "long":
        if not (ma50 > ma200 and last_price > ma200):
            return None
    else:
        if not (ma50 < ma200 and last_price < ma200):
            return None

    # --- 4) Filtres RSI & MACD (seuils élargis 40/60) ---
    rsi = compute_rsi(df['close'], 14).iloc[-1]
    macd, signal_line, _ = compute_macd(df['close'])
    macd_val   = macd.iloc[-1]
    signal_val = signal_line.iloc[-1]

    if side == "long":
        if not (rsi < 40 and macd_val > signal_val):
            return None
    else:
        if not (rsi > 60 and macd_val < signal_val):
            return None

    # --- 5) Filtre Fair Value Gap ---
    if side == "long":
        if not detect_fvg(df):
            return None
    else:
        if not detect_fvg_short(df):
            return None

    # --- 6) Calcul SL, TP1, TP2 (basé sur ATR) ---
    atr        = compute_atr(df, 14).iloc[-1]
    buffer_atr = atr * 0.2

    if side == "long":
        entry_price = entry_min
        stop_loss   = swing_low - buffer_atr
        rr          = entry_price - stop_loss
        tp1         = entry_price + rr * 1
        tp2         = entry_price + rr * 2
    else:
        entry_price = entry_max
        stop_loss   = swing_high + buffer_atr
        rr          = stop_loss - entry_price
        tp1         = entry_price - rr * 1
        tp2         = entry_price - rr * 2

    return {
        "entry_min":   float(entry_min),
        "entry_max":   float(entry_max),
        "entry_price": float(entry_price),
        "stop_loss":   float(stop_loss),
        "tp1":         float(tp1),
        "tp2":         float(tp2),
    }
