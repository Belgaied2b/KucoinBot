import pandas as pd

def detect_bos_cos(df, direction="long", lookback=20):
    if df is None or len(df) < lookback + 3 or not all(col in df.columns for col in ['high', 'low', 'close', 'volume']):
        return False, False

    df = df.copy()
    recent = df.iloc[-(lookback + 3):-3]
    prev_high = recent['high'].max()
    prev_low = recent['low'].min()

    candle = df.iloc[-1]
    volume_avg = df['volume'].rolling(window=20, min_periods=1).mean().iloc[-1]
    volume_ok = candle['volume'] > volume_avg * 1.2

    bos = cos = False

    if direction == "long":
        bos = candle['close'] > prev_high and volume_ok and (candle['close'] - candle['open']) > 0
        cos = candle['close'] < prev_low and volume_ok
    else:
        bos = candle['close'] < prev_low and volume_ok and (candle['open'] - candle['close']) > 0
        cos = candle['close'] > prev_high and volume_ok

    return bos, cos

def detect_choch(df, direction="long", lookback=20):
    if df is None or len(df) < lookback + 5 or not all(col in df.columns for col in ['high', 'low', 'close', 'volume']):
        return False

    df = df.copy()
    recent = df.iloc[-(lookback + 5):-5]
    prev_high = recent['high'].max()
    prev_low = recent['low'].min()

    candle = df.iloc[-1]
    volume_avg = df['volume'].rolling(window=20, min_periods=1).mean().iloc[-1]
    volume_ok = candle['volume'] > volume_avg * 1.2

    if direction == "long":
        # Un CHoCH haussier implique une cassure haussière après une structure baissière
        return candle['close'] > prev_high and volume_ok
    else:
        return candle['close'] < prev_low and volume_ok

def is_bos_valid(df, direction="long", lookback=20):
    bos, _ = detect_bos_cos(df, direction, lookback)

    if not bos:
        return False

    # Vérifie que le corps de la bougie dépasse un seuil (style institutional)
    candle = df.iloc[-1]
    atr = df['high'].rolling(14).max() - df['low'].rolling(14).min()
    atr_val = atr.iloc[-1] if not atr.isna().all() else 0

    body = abs(candle['close'] - candle['open'])
    return body > (atr_val * 0.5) if atr_val > 0 else True

def is_cos_valid(df, direction="long", lookback=20):
    _, cos = detect_bos_cos(df, direction, lookback)

    if not cos:
        return False

    # Détection d’un COS = réintégration rapide dans la structure
    candle = df.iloc[-1]
    previous = df.iloc[-2]

    if direction == "long":
        return previous['close'] > previous['low'] and candle['close'] < previous['low']
    else:
        return previous['close'] < previous['high'] and candle['close'] > previous['high']

def is_choch(df, direction="long", lookback=20):
    return detect_choch(df, direction, lookback)

def find_structure_tp(df, direction="long", entry_price=None):
    if df is None or len(df) < 10 or not all(col in df.columns for col in ['high', 'low']):
        return entry_price if entry_price is not None else 0

    swings = []
    window = 5
    for i in range(window, len(df) - window):
        hl = df['high' if direction == "long" else 'low']
        if direction == "long":
            if hl.iloc[i] > hl.iloc[i - window:i].max() and hl.iloc[i] > hl.iloc[i + 1:i + window + 1].max():
                swings.append(hl.iloc[i])
        else:
            if hl.iloc[i] < hl.iloc[i - window:i].min() and hl.iloc[i] < hl.iloc[i + 1:i + window + 1].min():
                swings.append(hl.iloc[i])

    if not swings:
        # fallback : structure simple
        if direction == "long":
            return df['high'].iloc[-20:].max()
        else:
            return df['low'].iloc[-20:].min()

    return max(swings) if direction == "long" else min(swings)

def is_bullish_engulfing(df):
    if df is None or len(df) < 2:
        return False

    prev = df.iloc[-2]
    curr = df.iloc[-1]

    return (
        prev['close'] < prev['open'] and
        curr['close'] > curr['open'] and
        curr['open'] < prev['close'] and
        curr['close'] > prev['open']
    )

def is_bearish_engulfing(df):
    if df is None or len(df) < 2:
        return False

    prev = df.iloc[-2]
    curr = df.iloc[-1]

    return (
        prev['close'] > prev['open'] and
        curr['close'] < curr['open'] and
        curr['open'] > prev['close'] and
        curr['close'] < prev['open']
    )
