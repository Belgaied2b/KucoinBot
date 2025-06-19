import pandas as pd

def detect_swing_points(df):
    """
    Détecte les points haut/bas structurels (HH/LL)
    """
    highs = df['high']
    lows = df['low']
    swing_highs = []
    swing_lows = []

    for i in range(2, len(df) - 2):
        if highs.iloc[i] > highs.iloc[i - 2] and highs.iloc[i] > highs.iloc[i - 1] and highs.iloc[i] > highs.iloc[i + 1] and highs.iloc[i] > highs.iloc[i + 2]:
            swing_highs.append((df.index[i], highs.iloc[i]))
        if lows.iloc[i] < lows.iloc[i - 2] and lows.iloc[i] < lows.iloc[i - 1] and lows.iloc[i] < lows.iloc[i + 1] and lows.iloc[i] < lows.iloc[i + 2]:
            swing_lows.append((df.index[i], lows.iloc[i]))

    return swing_highs, swing_lows

def is_bos_valid(df, direction):
    swing_highs, swing_lows = detect_swing_points(df)

    if direction == "long" and len(swing_highs) >= 2:
        last_high = swing_highs[-2][1]
        return df['close'].iloc[-1] > last_high
    elif direction == "short" and len(swing_lows) >= 2:
        last_low = swing_lows[-2][1]
        return df['close'].iloc[-1] < last_low
    return False

def is_cos_valid(df, direction):
    if len(df) < 30:
        return False

    swing_highs, swing_lows = detect_swing_points(df)

    if direction == "long" and len(swing_lows) >= 1:
        last_low = swing_lows[-1][1]
        return df['low'].iloc[-1] > last_low
    elif direction == "short" and len(swing_highs) >= 1:
        last_high = swing_highs[-1][1]
        return df['high'].iloc[-1] < last_high
    return False

def detect_bos_cos(df, direction):
    try:
        bos = is_bos_valid(df, direction)
        cos = is_cos_valid(df, direction)
        return bos, cos
    except Exception:
        return False, False

def detect_choch(df, direction):
    try:
        if len(df) < 50:
            return False

        swing_highs, swing_lows = detect_swing_points(df)

        if direction == "long" and len(swing_highs) >= 2:
            last_high = swing_highs[-2][1]
            return df['close'].iloc[-1] > last_high and df['volume'].iloc[-1] > df['volume'].rolling(20).mean().iloc[-1]
        elif direction == "short" and len(swing_lows) >= 2:
            last_low = swing_lows[-2][1]
            return df['close'].iloc[-1] < last_low and df['volume'].iloc[-1] > df['volume'].rolling(20).mean().iloc[-1]

        return False
    except Exception:
        return False
