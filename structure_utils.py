import pandas as pd
import numpy as np

def is_bos_valid(df, direction, window=20):
    """
    🔹 Break of Structure (BOS) :
    Cassure franche d’un sommet (long) ou d’un creux (short) avec clôture.
    """
    if df is None or len(df) < window + 5:
        return False

    highs = df['high'].rolling(window=window).max()
    lows = df['low'].rolling(window=window).min()

    if direction == "long":
        previous_high = highs.shift(1).iloc[-5]
        close_now = df['close'].iloc[-1]
        return close_now > previous_high
    else:
        previous_low = lows.shift(1).iloc[-5]
        close_now = df['close'].iloc[-1]
        return close_now < previous_low


def is_cos_valid(df, direction, window=10):
    """
    🔹 Confirmation of Structure (COS) :
    Le prix valide la cassure en tenant le niveau après le BOS.
    """
    if df is None or len(df) < window + 5:
        return False

    highs = df['high'].rolling(window=window).max()
    lows = df['low'].rolling(window=window).min()

    if direction == "long":
        support = lows.shift(1).iloc[-5]
        return df['low'].iloc[-1] > support
    else:
        resistance = highs.shift(1).iloc[-5]
        return df['high'].iloc[-1] < resistance


def detect_bos_cos(df, direction):
    """
    🔍 Combine BOS + COS avec logique stricte.
    """
    try:
        bos = is_bos_valid(df, direction)
        if not bos:
            return False, False
        cos = is_cos_valid(df, direction)
        return bos, cos
    except Exception:
        return False, False


def detect_choch(df, direction, lookback=10):
    """
    🔄 CHoCH (Change of Character) :
    Détection de retournement de structure.
    - long : cassure du dernier sommet + creux plus haut
    - short : cassure du dernier creux + sommet plus bas
    """
    try:
        if df is None or len(df) < lookback + 10:
            return False

        recent_close = df['close'].iloc[-1]
        recent_low = df['low'].iloc[-1]
        recent_high = df['high'].iloc[-1]

        prev_highs = df['high'].shift(1).rolling(window=lookback).max().iloc[-1]
        prev_lows = df['low'].shift(1).rolling(window=lookback).min().iloc[-1]

        if direction == "long":
            return recent_close > prev_highs and recent_low > df['low'].shift(1).iloc[-lookback]
        else:
            return recent_close < prev_lows and recent_high < df['high'].shift(1).iloc[-lookback]

    except Exception:
        return False
