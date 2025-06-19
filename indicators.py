import pandas as pd
import numpy as np

def compute_rsi(series, period=14):
    """RSI basé sur variation positive/négative moyenne (méthode standard)"""
    delta = series.diff()
    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)

    avg_gain = gain.ewm(alpha=1/period, min_periods=period).mean()
    avg_loss = loss.ewm(alpha=1/period, min_periods=period).mean()

    rs = avg_gain / avg_loss
    rsi = 100 - (100 / (1 + rs))
    return rsi

def compute_macd_histogram(series, fast=12, slow=26, signal=9):
    """MACD Histogram = MACD Line - Signal Line"""
    ema_fast = series.ewm(span=fast, min_periods=fast).mean()
    ema_slow = series.ewm(span=slow, min_periods=slow).mean()

    macd_line = ema_fast - ema_slow
    signal_line = macd_line.ewm(span=signal, min_periods=signal).mean()
    histogram = macd_line - signal_line

    return histogram

def compute_ma(df, period=200):
    """Moyenne mobile simple sur la clôture"""
    return df['close'].rolling(window=period, min_periods=period).mean()

def compute_atr(df, period=14):
    """Average True Range (ATR) - mesure de volatilité"""
    high = df['high']
    low = df['low']
    close = df['close']

    prev_close = close.shift(1)
    tr = pd.concat([
        (high - low),
        (high - prev_close).abs(),
        (low - prev_close).abs()
    ], axis=1).max(axis=1)

    atr = tr.rolling(window=period, min_periods=period).mean()
    return atr

def compute_fvg_zones(df, lookback=30):
    """
    Détection de FVG (Fair Value Gaps) simples :
    Bougie 1 (n-2), Bougie 2 (n-1), Bougie 3 (n)
    Si candle(n-1).high < candle(n).low => FVG haussier
    Si candle(n-1).low > candle(n).high => FVG baissier
    """
    fvg_upper = []
    fvg_lower = []

    for i in range(len(df)):
        if i < 2:
            fvg_upper.append(np.nan)
            fvg_lower.append(np.nan)
            continue

        prev2 = df.iloc[i-2]
        prev1 = df.iloc[i-1]
        curr = df.iloc[i]

        # FVG haussier
        if prev1['high'] < curr['low']:
            fvg_upper.append(curr['low'])
            fvg_lower.append(prev1['high'])

        # FVG baissier
        elif prev1['low'] > curr['high']:
            fvg_upper.append(prev1['low'])
            fvg_lower.append(curr['high'])

        else:
            fvg_upper.append(np.nan)
            fvg_lower.append(np.nan)

    return pd.DataFrame({
        'fvg_upper': fvg_upper,
        'fvg_lower': fvg_lower
    }, index=df.index)
