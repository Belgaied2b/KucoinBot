import pandas as pd
import numpy as np

def compute_macd_histogram(close_series, fast=12, slow=26, signal=9):
    exp1 = close_series.ewm(span=fast, adjust=False).mean()
    exp2 = close_series.ewm(span=slow, adjust=False).mean()
    macd = exp1 - exp2
    signal_line = macd.ewm(span=signal, adjust=False).mean()
    histogram = macd - signal_line
    return histogram

def compute_rsi(close_series, period=14):
    delta = close_series.diff()
    gain = delta.clip(lower=0).rolling(window=period).mean()
    loss = -delta.clip(upper=0).rolling(window=period).mean()
    rs = gain / loss
    rsi = 100 - (100 / (1 + rs))
    return rsi

def compute_ma(df, period=200):
    return df['close'].rolling(window=period).mean()

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
    return atr

def compute_fvg_zones(df):
    """
    Fair Value Gaps (FVG) détectés à partir des bougies :
    - FVG haussier si low[i-2] > high[i]
    - FVG baissier si high[i-2] < low[i]
    """
    fvg_upper = [np.nan, np.nan]
    fvg_lower = [np.nan, np.nan]

    for i in range(2, len(df)):
        prev_low = df['low'].iloc[i - 2]
        prev_high = df['high'].iloc[i - 2]
        curr_low = df['low'].iloc[i]
        curr_high = df['high'].iloc[i]

        if prev_low > curr_high:  # FVG haussier
            fvg_lower.append(curr_high)
            fvg_upper.append(prev_low)
        elif prev_high < curr_low:  # FVG baissier
            fvg_lower.append(prev_high)
            fvg_upper.append(curr_low)
        else:
            fvg_lower.append(np.nan)
            fvg_upper.append(np.nan)

    return pd.DataFrame({
        'fvg_lower': fvg_lower,
        'fvg_upper': fvg_upper
    }, index=df.index)

def find_pivots(df, left=5, right=5):
    """
    Détection des pivots (haut/bas locaux) utilisés pour TP dynamiques.
    """
    highs = []
    lows = []

    for i in range(left, len(df) - right):
        window = df['high'].iloc[i - left:i + right + 1]
        if df['high'].iloc[i] == window.max():
            highs.append(i)

        window = df['low'].iloc[i - left:i + right + 1]
        if df['low'].iloc[i] == window.min():
            lows.append(i)

    return highs, lows
