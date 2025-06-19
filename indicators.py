import pandas as pd
import numpy as np

def compute_rsi(series: pd.Series, period: int = 14) -> pd.Series:
    delta = series.diff()
    gain = (delta.where(delta > 0, 0)).rolling(window=period).mean()
    loss = (-delta.where(delta < 0, 0)).rolling(window=period).mean()
    rs = gain / loss
    return 100 - (100 / (1 + rs))

def compute_macd_histogram(series: pd.Series, fast: int = 12, slow: int = 26, signal: int = 9) -> pd.Series:
    ema_fast = series.ewm(span=fast, min_periods=1).mean()
    ema_slow = series.ewm(span=slow, min_periods=1).mean()
    macd = ema_fast - ema_slow
    signal_line = macd.ewm(span=signal, min_periods=1).mean()
    histogram = macd - signal_line
    return histogram

def compute_ma(df: pd.DataFrame, period: int = 200) -> pd.Series:
    return df['close'].rolling(window=period).mean().bfill()

def compute_atr(df: pd.DataFrame, period: int = 14) -> pd.Series:
    high = df['high']
    low = df['low']
    close = df['close']
    tr1 = high - low
    tr2 = abs(high - close.shift())
    tr3 = abs(low - close.shift())
    tr = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)
    atr = tr.rolling(window=period).mean()
    return atr.bfill()

def compute_fvg_zones(df: pd.DataFrame) -> pd.DataFrame:
    fvg_upper = []
    fvg_lower = []

    for i in range(2, len(df)):
        prev_high = df['high'].iloc[i - 2]
        prev_low = df['low'].iloc[i - 2]
        current_open = df['open'].iloc[i]
        current_close = df['close'].iloc[i]

        # Fair Value Gap (bougie 0 ignore 1, compare à 2)
        if current_open > prev_high:
            fvg_upper.append(current_open)
            fvg_lower.append(prev_high)
        elif current_open < prev_low:
            fvg_upper.append(prev_low)
            fvg_lower.append(current_open)
        else:
            fvg_upper.append(None)
            fvg_lower.append(None)

    # Décaler pour aligner avec les index d’origine
    fvg_upper = [None, None] + fvg_upper
    fvg_lower = [None, None] + fvg_lower

    return pd.DataFrame({
        'fvg_upper': fvg_upper,
        'fvg_lower': fvg_lower
    }, index=df.index)
