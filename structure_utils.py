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
