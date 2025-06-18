import pandas as pd
import numpy as np

def compute_macd_histogram(close_series, fast=12, slow=26, signal=9):
    exp1 = close_series.ewm(span=fast, adjust=False).mean()
    exp2 = close_series.ewm(span=slow, adjust=False).mean()
    macd = exp1 - exp2
    signal_line = macd.ewm(span=signal, adjust=False).mean()
    histogram = macd - signal_line
    return histogram.fillna(0)

def compute_rsi(close_series, period=14):
    delta = close_series.diff()
    gain = delta.where(delta > 0, 0)
    loss = -delta.where(delta < 0, 0)
    avg_gain = gain.rolling(window=period).mean()
    avg_loss = loss.rolling(window=period).mean()
    rs = avg_gain / avg_loss.replace(0, np.nan)
    rsi = 100 - (100 / (1 + rs))
    return rsi.fillna(50)  # neutre si incalculable

def compute_ma(df, period=200):
    return df['close'].rolling(window=period).mean().fillna(method='bfill')

def compute_atr(df, period=14):
    high = df['high']
    low = df['low']
    close = df['close']
    prev_close = close.shift(1)

    tr1 = high - low
    tr2 = (high - prev_close).abs()
    tr3 = (low - prev_close).abs()

    tr = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)
    atr = tr.rolling(window=period).mean()
    return atr.fillna(method='bfill')

def compute_fvg_zones(df):
    fvg_upper = []
    fvg_lower = []

    for i in range(2, len(df)):
        prev_low = df['low'].iloc[i - 2]
        current_high = df['high'].iloc[i]

        prev_high = df['high'].iloc[i - 2]
        current_low = df['low'].iloc[i]

        if prev_low > current_high:
            fvg_lower.append(current_high)
            fvg_upper.append(prev_low)
        elif prev_high < current_low:
            fvg_upper.append(current_low)
            fvg_lower.append(prev_high)
        else:
            fvg_upper.append(np.nan)
            fvg_lower.append(np.nan)

    fvg_df = pd.DataFrame({
        'fvg_upper': [np.nan, np.nan] + fvg_upper,
        'fvg_lower': [np.nan, np.nan] + fvg_lower
    }, index=df.index)

    return fvg_df

def find_pivots(df, left=5, right=5):
    highs = []
    lows = []
    for i in range(left, len(df) - right):
        if df['high'].iloc[i] == max(df['high'].iloc[i - left:i + right + 1]):
            highs.append(i)
        if df['low'].iloc[i] == min(df['low'].iloc[i - left:i + right + 1]):
            lows.append(i)
    return highs, lows
