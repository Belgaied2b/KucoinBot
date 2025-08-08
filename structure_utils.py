import pandas as pd
import numpy as np
from indicators import compute_atr

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
        return candle['close'] > prev_high and volume_ok
    else:
        return candle['close'] < prev_low and volume_ok

def is_bos_valid(df, direction="long", lookback=20):
    bos, _ = detect_bos_cos(df, direction, lookback)
    if not bos:
        return False

    candle = df.iloc[-1]
    atr_series = compute_atr(df)
    atr_val = atr_series.iloc[-1] if not atr_series.isna().all() else 0
    body = abs(candle['close'] - candle['open'])

    return body > (atr_val * 0.5) if atr_val > 0 else True

def is_cos_valid(df, direction="long", lookback=20):
    _, cos = detect_bos_cos(df, direction, lookback)
    if not cos:
        return False

    candle = df.iloc[-1]
    previous = df.iloc[-2]

    if direction == "long":
        return previous['close'] > previous['low'] and candle['close'] < previous['low']
    else:
        return previous['close'] < previous['high'] and candle['close'] > previous['high']

def is_choch(df, direction="long", lookback=20):
    opposite_direction = "short" if direction == "long" else "long"
    for i in range(lookback, len(df)):
        sub_df = df.iloc[i - lookback:i]
        if is_bos_valid(sub_df, direction=opposite_direction, lookback=int(lookback / 2)):
            return detect_choch(df, direction, lookback)
    return False

def is_choch_multi_tf(df_lower, df_higher, direction="long", lookback=20):
    """
    Valide CHoCH sur le TF inférieur uniquement si BOS opposé confirmé sur TF supérieur
    """
    choch = is_choch(df_lower, direction, lookback)
    bos_opposite = is_bos_valid(df_higher, "short" if direction == "long" else "long", lookback)
    return choch and bos_opposite

def find_structure_tp(df, direction="long", entry_price=None):
    if df is None or len(df) < 10 or not all(col in df.columns for col in ['high', 'low']):
        return entry_price if entry_price is not None else 0

    swings = []
    window = 5
    for i in range(window, len(df) - window):
        hl = df['high'] if direction == "long" else df['low']
        if direction == "long":
            if hl.iloc[i] > hl.iloc[i - window:i].max() and hl.iloc[i] > hl.iloc[i + 1:i + window + 1].max():
                swings.append(hl.iloc[i])
        else:
            if hl.iloc[i] < hl.iloc[i - window:i].min() and hl.iloc[i] < hl.iloc[i + 1:i + window + 1].min():
                swings.append(hl.iloc[i])

    if not swings:
        return df['high'].iloc[-20:].max() if direction == "long" else df['low'].iloc[-20:].min()

    return max(swings) if direction == "long" else min(swings)

def is_bullish_engulfing(df):
    if df is None or len(df) < 2:
        return False
    prev, curr = df.iloc[-2], df.iloc[-1]
    return (
        prev['close'] < prev['open'] and
        curr['close'] > curr['open'] and
        curr['open'] < prev['close'] and
        curr['close'] > prev['open']
    )

def is_bearish_engulfing(df):
    if df is None or len(df) < 2:
        return False
    prev, curr = df.iloc[-2], df.iloc[-1]
    return (
        prev['close'] > prev['open'] and
        curr['close'] < curr['open'] and
        curr['open'] > prev['close'] and
        curr['close'] < prev['open']
    )

def run_structure_tests():
    print("🔍 Lancement des tests structure_utils...\n")

    data = {
        'open': [100, 101, 102, 103, 104, 106, 105],
        'high': [102, 103, 104, 106, 108, 109, 110],
        'low': [98, 99, 100, 101, 102, 104, 103],
        'close': [101, 102, 103, 105, 107, 108, 109],
        'volume': [100, 120, 130, 150, 180, 200, 210]
    }
    df_test = pd.DataFrame(data)

    print("✅ BOS long :", is_bos_valid(df_test, "long"))
    print("✅ COS short :", is_cos_valid(df_test, "short"))
    print("✅ CHoCH long :", is_choch(df_test, "long"))
    print("✅ CHoCH multi-TF long :", is_choch_multi_tf(df_test, df_test, "long"))
    print("✅ TP long :", find_structure_tp(df_test, "long", entry_price=105))
    print("✅ Engulfing haussier :", is_bullish_engulfing(df_test))
    print("✅ Engulfing baissier :", is_bearish_engulfing(df_test))
    print("-" * 50 + "\n")

if __name__ == "__main__":
    run_structure_tests()
