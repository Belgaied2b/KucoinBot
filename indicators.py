import numpy as np
import pandas as pd

def calculate_ma(df, period=200):
    df[f"ma_{period}"] = df['close'].rolling(window=period).mean()
    return df

def is_above_ma200(df):
    ma200 = df['close'].rolling(window=200).mean()
    return df['close'].iloc[-1] > ma200.iloc[-1]

def is_below_ma200(df):
    ma200 = df['close'].rolling(window=200).mean()
    return df['close'].iloc[-1] < ma200.iloc[-1]

def calculate_atr(df, period=14):
    df['H-L'] = df['high'] - df['low']
    df['H-PC'] = abs(df['high'] - df['close'].shift(1))
    df['L-PC'] = abs(df['low'] - df['close'].shift(1))
    tr = df[['H-L', 'H-PC', 'L-PC']].max(axis=1)
    atr = tr.rolling(window=period).mean()
    return atr.iloc[-1] if not atr.empty else None

def calculate_macd_histogram(df, short_period=12, long_period=26, signal_period=9):
    exp1 = df['close'].ewm(span=short_period, adjust=False).mean()
    exp2 = df['close'].ewm(span=long_period, adjust=False).mean()
    macd = exp1 - exp2
    signal = macd.ewm(span=signal_period, adjust=False).mean()
    hist = macd - signal
    return hist

def calculate_rsi(df, period=14):
    delta = df['close'].diff()
    gain = delta.where(delta > 0, 0.0)
    loss = -delta.where(delta < 0, 0.0)

    avg_gain = gain.rolling(window=period, min_periods=period).mean()
    avg_loss = loss.rolling(window=period, min_periods=period).mean()

    rs = avg_gain / avg_loss
    rsi = 100 - (100 / (1 + rs))
    return rsi

def detect_ote_zone(df, direction):
    try:
        swing_low = df['low'].iloc[-30:-1].min()
        swing_high = df['high'].iloc[-30:-1].max()

        if direction == 'long':
            fib_618 = swing_low + 0.618 * (swing_high - swing_low)
            fib_786 = swing_low + 0.786 * (swing_high - swing_low)
            return (round(fib_786, 4), round(fib_618, 4))
        else:
            fib_1272 = swing_high - 0.272 * (swing_high - swing_low)
            fib_1618 = swing_high - 0.618 * (swing_high - swing_low)
            return (round(fib_1272, 4), round(fib_1618, 4))
    except:
        return None

def detect_fvg(df, direction):
    try:
        for i in range(len(df) - 3, 1, -1):
            prev_low = df['low'].iloc[i - 1]
            curr_high = df['high'].iloc[i]
            next_low = df['low'].iloc[i + 1]
            prev_high = df['high'].iloc[i - 1]
            curr_low = df['low'].iloc[i]
            next_high = df['high'].iloc[i + 1]

            if direction == 'long':
                if curr_low > prev_high and curr_low > next_high:
                    return (round(prev_high, 4), round(curr_low, 4))
            else:
                if curr_high < prev_low and curr_high < next_low:
                    return (round(curr_high, 4), round(prev_low, 4))
        return None
    except:
        return None

def is_price_in_ote_zone(df, ote_zone):
    if ote_zone is None:
        return False
    current_price = df['close'].iloc[-1]
    lower, upper = ote_zone
    return lower <= current_price <= upper

def detect_divergence(df):
    try:
        rsi = calculate_rsi(df, period=14)
        lows = df['low']
        highs = df['high']

        if lows.iloc[-2] < lows.iloc[-4] and rsi.iloc[-2] > rsi.iloc[-4]:
            return 'bullish'
        if highs.iloc[-2] > highs.iloc[-4] and rsi.iloc[-2] < rsi.iloc[-4]:
            return 'bearish'
        return None
    except:
        return None
