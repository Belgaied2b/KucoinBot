import pandas as pd

def is_bos_valid(df, direction):
    """
    Break of Structure (BOS) : le prix casse un plus haut (long) ou plus bas (short) précédent.
    """
    if len(df) < 30:
        return False

    highs = df['high'].rolling(window=20).max()
    lows = df['low'].rolling(window=20).min()

    if direction == "long":
        previous_high = highs.shift(1).iloc[-5]
        return df['close'].iloc[-1] > previous_high
    else:
        previous_low = lows.shift(1).iloc[-5]
        return df['close'].iloc[-1] < previous_low

def is_cos_valid(df, direction):
    """
    Confirmation of Structure (COS) : après cassure BOS, le marché tient la structure.
    """
    if len(df) < 30:
        return False

    highs = df['high'].rolling(window=10).max()
    lows = df['low'].rolling(window=10).min()

    if direction == "long":
        previous_low = lows.shift(1).iloc[-5]
        return df['low'].iloc[-1] > previous_low
    else:
        previous_high = highs.shift(1).iloc[-5]
        return df['high'].iloc[-1] < previous_high

def detect_bos_cos(df, direction):
    """
    Retourne le statut BOS et COS (True/False).
    """
    try:
        bos = is_bos_valid(df, direction)
        cos = is_cos_valid(df, direction)
        return bos, cos
    except Exception:
        return False, False

def detect_choch(df, direction):
    """
    Change of Character (CHoCH) : retournement de tendance.
    Exemple : tendance baissière avec cassure haussière (long).
    """
    try:
        if len(df) < 40:
            return False

        highs = df['high'].rolling(window=10).max()
        lows = df['low'].rolling(window=10).min()

        if direction == "long":
            choch_up = df['close'].iloc[-1] > df['high'].iloc[-10] and df['low'].iloc[-1] > lows.shift(1).iloc[-10]
            return choch_up
        else:
            choch_down = df['close'].iloc[-1] < df['low'].iloc[-10] and df['high'].iloc[-1] < highs.shift(1).iloc[-10]
            return choch_down
    except Exception:
        return False
