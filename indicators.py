# indicators.py

import pandas as pd

def compute_rsi(df: pd.DataFrame, period: int = 14) -> pd.Series:
    """
    Relative Strength Index (RSI)
    """
    delta = df['close'].diff()
    up = delta.clip(lower=0)
    down = -delta.clip(upper=0)
    ma_up = up.ewm(com=period-1, adjust=False).mean()
    ma_down = down.ewm(com=period-1, adjust=False).mean()
    rs = ma_up / ma_down
    rsi = 100 - (100 / (1 + rs))
    return rsi

def compute_macd(df: pd.DataFrame, fast: int = 12, slow: int = 26, signal: int = 9) -> pd.DataFrame:
    """
    MACD Line, Signal Line, Histogram
    """
    ema_fast = df['close'].ewm(span=fast, adjust=False).mean()
    ema_slow = df['close'].ewm(span=slow, adjust=False).mean()
    macd_line = ema_fast - ema_slow
    signal_line = macd_line.ewm(span=signal, adjust=False).mean()
    histogram = macd_line - signal_line
    return pd.DataFrame({
        'macd': macd_line,
        'signal': signal_line,
        'hist': histogram
    })

def compute_atr(df: pd.DataFrame, period: int = 14) -> pd.Series:
    """
    Average True Range (ATR)
    """
    high_low = df['high'] - df['low']
    high_close = (df['high'] - df['close'].shift()).abs()
    low_close = (df['low'] - df['close'].shift()).abs()
    tr = pd.concat([high_low, high_close, low_close], axis=1).max(axis=1)
    atr = tr.ewm(span=period, adjust=False).mean()
    return atr

def find_pivots(df: pd.DataFrame, window: int = 5):
    """
    Détecte pivots hauts et bas sur une fenêtre glissante.
    - window: nombre de bougies avant/après pour comparaison.
    Retourne deux listes d’indices: highs, lows.
    """
    highs, lows = [], []
    for i in range(window, len(df) - window):
        slice_high = df['high'].iloc[i-window:i+window+1]
        slice_low  = df['low'].iloc[i-window:i+window+1]

        if df['high'].iloc[i] == slice_high.max():
            highs.append(i)
        if df['low'].iloc[i] == slice_low.min():
            lows.append(i)

    return highs, lows
