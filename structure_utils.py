import pandas as pd
import numpy as np

def is_bos_valid(df, direction):
    """Détecte un Break of Structure (BOS)."""
    highs = df['high'].rolling(window=20).max()
    lows = df['low'].rolling(window=20).min()

    if direction == "long":
        return df['close'].iloc[-1] > highs.shift(1).iloc[-5]
    else:
        return df['close'].iloc[-1] < lows.shift(1).iloc[-5]

def is_cos_valid(df, direction):
    """Détecte une Confirmation of Structure (COS)."""
    highs = df['high'].rolling(window=10).max()
    lows = df['low'].rolling(window=10).min()

    if direction == "long":
        return df['low'].iloc[-1] > lows.shift(1).iloc[-5]
    else:
        return df['high'].iloc[-1] < highs.shift(1).iloc[-5]

def detect_bos_cos(df, direction):
    """Valide BOS + COS."""
    try:
        bos = is_bos_valid(df, direction)
        cos = is_cos_valid(df, direction)
        return bos, cos
    except Exception:
        return False, False

def detect_choch(df, direction):
    """Détecte un CHoCH (Change of Character)."""
    try:
        if len(df) < 50:
            return False

        highs = df['high'].rolling(window=10).max()
        lows = df['low'].rolling(window=10).min()

        if direction == "long":
            prev_low = lows.iloc[-10]
            return df['close'].iloc[-1] > df['high'].iloc[-10] and df['low'].iloc[-1] > prev_low
        else:
            prev_high = highs.iloc[-10]
            return df['close'].iloc[-1] < df['low'].iloc[-10] and df['high'].iloc[-1] < prev_high
    except Exception:
        return False
