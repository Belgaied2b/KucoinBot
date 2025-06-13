import pandas as pd

def compute_rsi(df: pd.DataFrame, period: int = 14) -> pd.Series:
    delta = df['close'].diff()
    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)
    avg_gain = gain.rolling(window=period, min_periods=1).mean()
    avg_loss = loss.rolling(window=period, min_periods=1).mean()
    rs = avg_gain / avg_loss.replace({0: 1e-10})
    return 100 - (100 / (1 + rs))

def compute_macd(df: pd.DataFrame, fast: int = 12, slow: int = 26, signal: int = 9) -> pd.DataFrame:
    exp1 = df['close'].ewm(span=fast, adjust=False).mean()
    exp2 = df['close'].ewm(span=slow, adjust=False).mean()
    macd_val = exp1 - exp2
    signal_line = macd_val.ewm(span=signal, adjust=False).mean()
    return pd.DataFrame({
        'macd': macd_val,
        'signal': signal_line,
        'histogram': macd_val - signal_line
    }, index=df.index)

def compute_macd_histogram(df, fast=12, slow=26, signal=9):
    """
    Calcule uniquement l'histogramme MACD (MACD - signal).
    Accepte une Series (close) ou un DataFrame contenant 'close'.
    """
    if isinstance(df, pd.Series):
        close = df
    elif isinstance(df, pd.DataFrame) and 'close' in df.columns:
        close = df['close']
    else:
        raise ValueError("compute_macd_histogram() requiert une Series ou un DataFrame avec 'close'")

    ema_fast = close.ewm(span=fast, adjust=False).mean()
    ema_slow = close.ewm(span=slow, adjust=False).mean()
    macd = ema_fast - ema_slow
    signal_line = macd.ewm(span=signal, adjust=False).mean()
    return macd - signal_line

def compute_fvg(df: pd.DataFrame) -> pd.DataFrame:
    fvg_upper = []
    fvg_lower = []

    for i in range(1, len(df) - 1):
        high_prev = df['high'].iloc[i - 1]
        low_next = df['low'].iloc[i + 1]
        low_prev = df['low'].iloc[i - 1]
        high_next = df['high'].iloc[i + 1]

        if low_next > high_prev:
            fvg_upper.append(low_next)
            fvg_lower.append(high_prev)
        elif high_next < low_prev:
            fvg_upper.append(low_prev)
            fvg_lower.append(high_next)
        else:
            fvg_upper.append(None)
            fvg_lower.append(None)

    fvg_df = pd.DataFrame({
        'fvg_upper': [None] + fvg_upper + [None],
        'fvg_lower': [None] + fvg_lower + [None]
    }, index=df.index)

    fvg_df = fvg_df.ffill().bfill()
    return fvg_df

def compute_ote(df: pd.DataFrame, window: int = 20) -> pd.DataFrame:
    high_ = df['high'].rolling(window=window, min_periods=1).max()
    low_  = df['low'].rolling(window=window, min_periods=1).min()
    ote_upper = low_ + (high_ - low_) * 0.705
    ote_lower = low_ + (high_ - low_) * 0.62
    return pd.DataFrame({
        'ote_upper': ote_upper,
        'ote_lower': ote_lower
    }, index=df.index)

def compute_atr(df: pd.DataFrame, period: int = 14) -> pd.Series:
    high_low        = df['high'] - df['low']
    high_close_prev = (df['high'] - df['close'].shift()).abs()
    low_close_prev  = (df['low']  - df['close'].shift()).abs()
    tr = pd.concat([high_low, high_close_prev, low_close_prev], axis=1).max(axis=1)
    return tr.rolling(window=period, min_periods=1).mean()

def compute_ma(df: pd.DataFrame, period: int = 200) -> pd.Series:
    return df['close'].rolling(window=period, min_periods=1).mean()

def find_pivots(df: pd.DataFrame, window: int = 5):
    highs = []
    lows = []
    for i in range(window, len(df) - window):
        if df['high'].iloc[i] == df['high'].iloc[i - window:i + window + 1].max():
            highs.append(i)
        if df['low'].iloc[i] == df['low'].iloc[i - window:i + window + 1].min():
            lows.append(i)
    return highs, lows
