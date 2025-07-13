import pandas as pd
import numpy as np

def compute_rsi(series, period=14):
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
    return df['close'].rolling(window=period, min_periods=period).mean()


def compute_atr(df, period=14):
    if df is None or len(df) < period or not all(x in df.columns for x in ['high', 'low', 'close']):
        return pd.Series([np.nan] * len(df), index=df.index)

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
        prev2 = df.iloc[i - 2]
        prev1 = df.iloc[i - 1]
        curr = df.iloc[i]

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
    Détecte une divergence simple entre le prix et le RSI.
    """
    if df is None or len(df) < window or "close" not in df.columns:
        return False

    if "rsi" not in df.columns:
        df["rsi"] = compute_rsi(df["close"])

    prices = df["close"].iloc[-window:]
    rsis = df["rsi"].iloc[-window:]

    if prices.empty or rsis.empty or len(prices) < 5:
        return False

    try:
        if direction == "long":
            price_low = prices.idxmin()
            rsi_at_price_low = rsis.loc[price_low]

            next_low = prices[price_low:].idxmin()
            rsi_next = rsis.loc[next_low]

            return rsi_next > rsi_at_price_low
        else:
            price_high = prices.idxmax()
            rsi_at_price_high = rsis.loc[price_high]

            next_high = prices[price_high:].idxmax()
            rsi_next = rsis.loc[next_high]

            return rsi_next < rsi_at_price_high
    except Exception as e:
        print(f"⚠️ Erreur divergence sur {df.name if hasattr(df, 'name') else 'symbole inconnu'} : {e}")
        return False
