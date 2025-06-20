import pandas as pd

def detect_bos_cos(df, direction="long", lookback=20):
    """
    Détecte un Break of Structure (BOS) et un Change of Structure (COS)
    en analysant les cassures de plus hauts ou plus bas significatifs.
    """
    if df is None or len(df) < lookback:
        return False, False

    highs = df['high'].rolling(window=lookback).max()
    lows = df['low'].rolling(window=lookback).min()

    last_high = highs.iloc[-2]
    last_low = lows.iloc[-2]
    close = df['close'].iloc[-1]

    bos = False
    cos = False

    if direction == "long":
        if close > last_high:
            bos = True
        if close < last_low:
            cos = True
    else:
        if close < last_low:
            bos = True
        if close > last_high:
            cos = True

    return bos, cos


def detect_choch(df, direction="long", lookback=20):
    """
    Détecte un Change of Character (CHoCH) :
    Indique un retournement potentiel par cassure inverse de structure précédente.
    """
    if df is None or len(df) < lookback + 1:
        return False

    recent_highs = df['high'].rolling(window=lookback).max()
    recent_lows = df['low'].rolling(window=lookback).min()
    close = df['close'].iloc[-1]

    # On vérifie une cassure inverse par rapport à la tendance actuelle
    if direction == "long":
        previous_lows = recent_lows.shift(1)
        choch = close < previous_lows.iloc[-1]
    else:
        previous_highs = recent_highs.shift(1)
        choch = close > previous_highs.iloc[-1]

    return choch
