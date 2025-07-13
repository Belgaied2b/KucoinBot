import pandas as pd
import numpy as np

def compute_rsi(series, period=14):
    series = pd.to_numeric(series, errors='coerce')
    if series is None or len(series) < period:
        return pd.Series([np.nan] * len(series), index=series.index)

    delta = series.diff()
    gain = delta.where(delta > 0, 0.0)
    loss = -delta.where(delta < 0, 0.0)

    avg_gain = gain.ewm(alpha=1/period, min_periods=period, adjust=False).mean()
    avg_loss = loss.ewm(alpha=1/period, min_periods=period, adjust=False).mean()

    rs = avg_gain / avg_loss.replace(0, np.nan)
    rsi = 100 - (100 / (1 + rs))
    return rsi.fillna(0)


def compute_macd_histogram(series, fast=12, slow=26, signal=9):
    series = pd.to_numeric(series, errors='coerce')
    if series is None or len(series) < slow + signal:
        return pd.Series([np.nan] * len(series), index=series.index)

    ema_fast = series.ewm(span=fast, min_periods=fast, adjust=False).mean()
    ema_slow = series.ewm(span=slow, min_periods=slow, adjust=False).mean()

    macd_line = ema_fast - ema_slow
    signal_line = macd_line.ewm(span=signal, min_periods=signal, adjust=False).mean()
    histogram = macd_line - signal_line

    return histogram.fillna(0)


def compute_ma(df, period=200):
    if df is None or 'close' not in df.columns or len(df) < period:
        return pd.Series([np.nan] * len(df), index=df.index)
    df['close'] = pd.to_numeric(df['close'], errors='coerce')
    return df['close'].rolling(window=period, min_periods=period).mean()


def compute_atr(df, period=14):
    if df is None or len(df) < period or not all(x in df.columns for x in ['high', 'low', 'close']):
        return pd.Series([np.nan] * len(df), index=df.index)

    df['high'] = pd.to_numeric(df['high'], errors='coerce')
    df['low'] = pd.to_numeric(df['low'], errors='coerce')
    df['close'] = pd.to_numeric(df['close'], errors='coerce')

    prev_close = df['close'].shift(1)
    tr1 = df['high'] - df['low']
    tr2 = (df['high'] - prev_close).abs()
    tr3 = (df['low'] - prev_close).abs()
    tr = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)

    atr = tr.rolling(window=period, min_periods=period).mean()
    return atr.bfill()


def compute_fvg_zones(df, lookback=30):
    if df is None or len(df) < 3 or not all(k in df.columns for k in ['high', 'low']):
        return pd.DataFrame({'fvg_upper': [np.nan]*len(df), 'fvg_lower': [np.nan]*len(df)}, index=df.index)

    fvg_upper = [np.nan] * len(df)
    fvg_lower = [np.nan] * len(df)

    for i in range(2, len(df)):
        prev1 = df.iloc[i - 1]
        curr = df.iloc[i]

        if pd.isna(prev1['high']) or pd.isna(curr['low']):
            continue

        # FVG haussier
        if prev1['high'] < curr['low']:
            fvg_upper[i] = curr['low']
            fvg_lower[i] = prev1['high']
        # FVG baissier
        elif prev1['low'] > curr['high']:
            fvg_upper[i] = prev1['low']
            fvg_lower[i] = curr['high']

    return pd.DataFrame({
        'fvg_upper': fvg_upper,
        'fvg_lower': fvg_lower
    }, index=df.index)


def detect_divergence(df, direction="long", window=20):
    """
    Détecte une divergence RSI ou MACD entre le prix et l’indicateur.
    """
    if df is None or len(df) < window + 5:
        return False

    try:
        if not all(col in df.columns for col in ['close', 'rsi', 'macd_histogram']):
            return False

        df = df.copy()
        df['close'] = pd.to_numeric(df['close'], errors='coerce')
        df['rsi'] = pd.to_numeric(df['rsi'], errors='coerce')
        df['macd_histogram'] = pd.to_numeric(df['macd_histogram'], errors='coerce')
        df.dropna(subset=['close', 'rsi', 'macd_histogram'], inplace=True)

        if len(df) < window + 5:
            return False

        recent = df.iloc[-window:]

        if direction == "long":
            price_min_idx = recent['close'].idxmin()
            rsi_min_idx = recent['rsi'].idxmin()
            macd_min_idx = recent['macd_histogram'].idxmin()

            price_div = df['close'].iloc[-1] > df['close'].loc[price_min_idx]
            rsi_div = df['rsi'].iloc[-1] > df['rsi'].loc[rsi_min_idx]
            macd_div = df['macd_histogram'].iloc[-1] > df['macd_histogram'].loc[macd_min_idx]
        else:
            price_max_idx = recent['close'].idxmax()
            rsi_max_idx = recent['rsi'].idxmax()
            macd_max_idx = recent['macd_histogram'].idxmax()

            price_div = df['close'].iloc[-1] < df['close'].loc[price_max_idx]
            rsi_div = df['rsi'].iloc[-1] < df['rsi'].loc[rsi_max_idx]
            macd_div = df['macd_histogram'].iloc[-1] < df['macd_histogram'].loc[macd_max_idx]

        return price_div and (rsi_div or macd_div)

    except Exception as e:
        print(f"⚠️ Erreur divergence sur {df.name if hasattr(df, 'name') else 'symbole inconnu'} : {e}")
        return False
