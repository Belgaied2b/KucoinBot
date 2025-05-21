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

def compute_fvg(df: pd.DataFrame, window: int = 5) -> pd.DataFrame:
    fvg_upper = df['high'].shift(1) - df['low'].shift(-1)
    fvg_lower = df['low'].shift(1) - df['high'].shift(-1)
    return pd.DataFrame({
        'fvg_upper': fvg_upper,
        'fvg_lower': fvg_lower
    }, index=df.index)

def compute_ote(df: pd.DataFrame, window: int = 5) -> pd.DataFrame:
    high_ = df['high'].rolling(window=window, min_periods=1).max()
    low_  = df['low'].rolling(window=window, min_periods=1).min()
    ote_upper = high_ - (high_ - low_) * 0.38
    ote_lower = low_  + (high_ - low_) * 0.38
    return pd.DataFrame({
        'ote_upper': ote_upper,
        'ote_lower': ote_lower
    }, index=df.index)

def compute_atr(df: pd.DataFrame, period: int = 14) -> pd.Series:
    high_low        = df['high'] - df['low']
    high_prev_close = (df['high'] - df['close'].shift()).abs()
    low_prev_close  = (df['low']  - df['close'].shift()).abs()
    tr = pd.concat([high_low, high_prev_close, low_prev_close], axis=1).max(axis=1)
    return tr.rolling(window=period, min_periods=1).mean()

def compute_ma(df: pd.DataFrame, period: int = 200) -> pd.Series:
    return df['close'].rolling(window=period, min_periods=1).mean()

def find_pivots(df: pd.DataFrame, window: int = 5):
    highs, lows = [], []
    for i in range(window, len(df) - window):
        if df['high'].iloc[i] == df['high'].iloc[i-window:i+window+1].max():
            highs.append(i)
        if df['low'].iloc[i] == df['low'].iloc[i-window:i+window+1].min():
            lows.append(i)
    return highs, lows
