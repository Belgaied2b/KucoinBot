import pandas as pd
import numpy as np


def compute_rsi(series, period=14):
    if series is None or len(series) < period:
        return pd.Series([np.nan] * len(series), index=series.index if series is not None else None)
    delta = series.diff()
    gain = delta.where(delta > 0, 0.0)
    loss = -delta.where(delta < 0, 0.0)
    avg_gain = gain.ewm(alpha=1 / period, min_periods=period, adjust=False).mean()
    avg_loss = loss.ewm(alpha=1 / period, min_periods=period, adjust=False).mean()
    rs = avg_gain / avg_loss.replace(0, np.nan)
    rsi = 100 - (100 / (1 + rs))
    return rsi.fillna(0)


def compute_macd_histogram(series, fast=12, slow=26, signal=9):
    if series is None or len(series) < slow + signal:
        return pd.Series([np.nan] * len(series), index=series.index if series is not None else None)
    ema_fast = series.ewm(span=fast, min_periods=fast, adjust=False).mean()
    ema_slow = series.ewm(span=slow, min_periods=slow, adjust=False).mean()
    macd_line = ema_fast - ema_slow
    signal_line = macd_line.ewm(span=signal, min_periods=signal, adjust=False).mean()
    histogram = macd_line - signal_line
    return histogram.fillna(0)


def compute_ma(df, period=200, method='sma'):
    if df is None or 'close' not in df.columns or len(df) < period:
        return pd.Series([np.nan] * len(df), index=df.index if df is not None else None)
    if method == 'ema':
        return df['close'].ewm(span=period, adjust=False).mean()
    return df['close'].rolling(window=period, min_periods=period).mean()


def compute_atr(df, period=14):
    if df is None or len(df) < period or not all(x in df.columns for x in ['high', 'low', 'close']):
        return pd.Series([np.nan] * len(df), index=df.index if df is not None else None)
    prev_close = df['close'].shift(1)
    tr1 = df['high'] - df['low']
    tr2 = (df['high'] - prev_close).abs()
    tr3 = (df['low'] - prev_close).abs()
    tr = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)
    atr = tr.ewm(span=period, min_periods=period, adjust=False).mean()
    return atr.bfill()


def compute_fvg_zones(df, lookback=30):
    if df is None or len(df) < 3 or not all(k in df.columns for k in ['high', 'low']):
        return pd.DataFrame({'fvg_upper': [np.nan] * len(df), 'fvg_lower': [np.nan] * len(df)}, index=df.index)
    fvg_upper = [np.nan] * len(df)
    fvg_lower = [np.nan] * len(df)
    for i in range(2, len(df)):
        prev = df.iloc[i - 1]
        curr = df.iloc[i]
        if prev['high'] < curr['low']:
            fvg_upper[i] = curr['low']
            fvg_lower[i] = prev['high']
        elif prev['low'] > curr['high']:
            fvg_upper[i] = prev['low']
            fvg_lower[i] = curr['high']
    return pd.DataFrame({'fvg_upper': fvg_upper, 'fvg_lower': fvg_lower}, index=df.index)


def is_volume_strong(df, window=20, multiplier=1.2):
    if df is None or 'volume' not in df.columns or len(df) < window:
        return False
    avg_volume = df['volume'].rolling(window=window, min_periods=1).mean().iloc[-1]
    return df['volume'].iloc[-1] > avg_volume * multiplier


def is_above_ma200(df):
    if df is None or 'close' not in df.columns or len(df) < 200:
        return False
    ma200 = compute_ma(df, period=200)
    return df['close'].iloc[-1] > ma200.iloc[-1]


def is_below_ma200(df):
    if df is None or 'close' not in df.columns or len(df) < 200:
        return False
    ma200 = compute_ma(df, period=200)
    return df['close'].iloc[-1] < ma200.iloc[-1]


def is_macd_positive(df):
    macd_hist = compute_macd_histogram(df['close'])
    return macd_hist.iloc[-1] > 0


def is_macd_negative(df):
    macd_hist = compute_macd_histogram(df['close'])
    return macd_hist.iloc[-1] < 0


def is_atr_sufficient(df, min_ratio=0.005):
    atr = compute_atr(df)
    if atr.isna().all():
        return False
    return atr.iloc[-1] > df['close'].iloc[-1] * min_ratio


def is_total_ok(total_df, direction="long"):
    if total_df is None or len(total_df) < 3:
        return False
    last = total_df['close'].iloc[-1]
    prev = total_df['close'].iloc[-3]
    return last > prev if direction == "long" else last < prev


def is_btc_ok(btc_df):
    if btc_df is None or len(btc_df) < 3:
        return False
    close = btc_df['close']
    return close.iloc[-1] > close.iloc[-2] > close.iloc[-3] or close.iloc[-1] < close.iloc[-2] < close.iloc[-3]


def is_bullish_divergence(df):
    if df is None or len(df) < 20:
        return False
    rsi = compute_rsi(df['close'])
    price_low = df['low']
    return price_low.iloc[-1] < price_low.iloc[-5] and rsi.iloc[-1] > rsi.iloc[-5]


def is_bearish_divergence(df):
    if df is None or len(df) < 20:
        return False
    rsi = compute_rsi(df['close'])
    price_high = df['high']
    return price_high.iloc[-1] > price_high.iloc[-5] and rsi.iloc[-1] < rsi.iloc[-5]


def get_btc_dominance_trend(btc_d_df):
    if btc_d_df is None or len(btc_d_df) < 3 or 'close' not in btc_d_df.columns:
        return "INCONNU"
    close = btc_d_df['close']
    if close.iloc[-1] > close.iloc[-2] > close.iloc[-3]:
        return "EN HAUSSE"
    elif close.iloc[-1] < close.iloc[-2] < close.iloc[-3]:
        return "EN BAISSE"
    else:
        return "STAGNANT"
