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

# 🔄 VOLUME ou Open Interest
def is_volume_strong(df, window=20, multiplier=1.2):
    column = 'open_interest' if 'open_interest' in df.columns else 'volume'
    if df is None or column not in df.columns or len(df) < window:
        return False
    avg = df[column].rolling(window=window, min_periods=1).mean().iloc[-1]
    return df[column].iloc[-1] > avg * multiplier

# 📉 EMA20 / EMA50 trend
def is_ema_trend_ok(df, direction="long"):
    if df is None or 'close' not in df.columns:
        return False
    ema20 = compute_ma(df, period=20, method='ema')
    ema50 = compute_ma(df, period=50, method='ema')
    if direction == "long":
        return ema20.iloc[-1] > ema50.iloc[-1]
    else:
        return ema20.iloc[-1] < ema50.iloc[-1]

# 🧠 MACD + RSI + Volume
def is_momentum_ok(df, direction="long"):
    macd = compute_macd_histogram(df['close']).iloc[-1]
    rsi = compute_rsi(df['close']).iloc[-1]
    vol_ok = is_volume_strong(df)
    if direction == "long":
        return macd > 0 and rsi > 50 and vol_ok
    else:
        return macd < 0 and rsi < 50 and vol_ok

# 💥 BOS + volume
def is_bos_with_strength(df, direction="long", lookback=20):
    if df is None or len(df) < lookback + 3:
        return False
    recent = df.iloc[-(lookback + 3):-3]
    prev_high = recent['high'].max()
    prev_low = recent['low'].min()
    candle = df.iloc[-1]
    vol_ok = is_volume_strong(df)
    if direction == "long":
        return candle['close'] > prev_high and vol_ok
    else:
        return candle['close'] < prev_low and vol_ok

# 🌀 COS + divergence ou volume
def is_cos_enhanced(df, direction="long", lookback=20):
    if df is None or len(df) < lookback + 3:
        return False
    recent = df.iloc[-(lookback + 3):-3]
    prev_high = recent['high'].max()
    prev_low = recent['low'].min()
    candle = df.iloc[-1]
    divergence = is_bullish_divergence(df) if direction == "long" else is_bearish_divergence(df)
    vol_ok = is_volume_strong(df)
    if direction == "long":
        return candle['close'] < prev_low and (divergence or vol_ok)
    else:
        return candle['close'] > prev_high and (divergence or vol_ok)

# 🪙 BTC key level
def is_btc_at_key_level(df, threshold_percent=0.01):
    if df is None or len(df) < 20:
        return False
    high = df['high'].rolling(window=20).max().iloc[-1]
    low = df['low'].rolling(window=20).min().iloc[-1]
    current = df['close'].iloc[-1]
    return abs(current - high) / high < threshold_percent or abs(current - low) / low < threshold_percent

# ✅ MA200 helpers
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

# ✅ Divergences RSI
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

# ✅ Macro
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

# 💥 Volume agressif (delta * volume)
def is_aggressive_volume_ok(df, direction="long", window=20):
    if df is None or len(df) < window + 1:
        return False
    df = df.copy()
    df['delta'] = df['close'] - df['open']
    df['aggressive'] = df['delta'] * df['volume']
    recent = df['aggressive'].iloc[-window:]
    avg = recent.mean()
    return avg > 0 if direction == "long" else avg < 0

# 💧 Liquidité (equal highs/lows)
def has_liquidity_zone(df, direction="long", window=20, tolerance=0.002):
    if df is None or len(df) < window:
        return False
    values = df['high'] if direction == "short" else df['low']
    recent = values.iloc[-window:]
    for i in range(len(recent) - 2):
        for j in range(i + 1, len(recent)):
            v1 = recent.iloc[i]
            v2 = recent.iloc[j]
            if abs(v1 - v2) / v1 < tolerance:
                return True
    return False

# ✅ Alias
is_liquidity_zone_present = has_liquidity_zone
