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
        bos = candle['close'] > prev_high and volume_ok
        cos = candle['close'] < prev_low and volume_ok
    else:
        bos = candle['close'] < prev_low and volume_ok
        cos = candle['close'] > prev_high and volume_ok

    return bos, cos

def detect_choch(df, direction="long", lookback=20):
    if df is None or len(df) < lookback + 3 or not all(col in df.columns for col in ['high', 'low', 'close', 'volume']):
        return False

    df = df.copy()
    recent = df.iloc[-(lookback + 3):-3]
    prev_high = recent['high'].max()
    prev_low = recent['low'].min()

    candle = df.iloc[-1]
    volume_avg = df['volume'].rolling(window=20, min_periods=1).mean().iloc[-1]
    volume_ok = candle['volume'] > volume_avg * 1.2

    if direction == "long":
        return candle['close'] < prev_low and volume_ok
    else:
        return candle['close'] > prev_high and volume_ok

def is_bos_valid(df, direction="long", lookback=20):
    bos, _ = detect_bos_cos(df, direction, lookback)
    return bos

def is_cos_valid(df, direction="long", lookback=20):
    _, cos = detect_bos_cos(df, direction, lookback)
    return cos

def is_choch(df, direction="long", lookback=20):
    return detect_choch(df, direction, lookback)

def find_structure_tp(df, direction="long", entry_price=None):
    if df is None or len(df) < 10 or 'high' not in df.columns or 'low' not in df.columns:
        return entry_price if entry_price is not None else 0

    highs = df['high'].iloc[-20:]
    lows = df['low'].iloc[-20:]

    if direction == "long":
        return highs.max()
    else:
        return lows.min()

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
