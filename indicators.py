import pandas as pd
import numpy as np

def compute_macd_histogram(close, short=12, long=26, signal=9):
    ema_short = close.ewm(span=short, adjust=False).mean()
    ema_long = close.ewm(span=long, adjust=False).mean()
    macd_line = ema_short - ema_long
    signal_line = macd_line.ewm(span=signal, adjust=False).mean()
    histogram = macd_line - signal_line
    return histogram.fillna(0)

def compute_rsi(close, period=14):
    delta = close.diff()
    gain = delta.where(delta > 0, 0)
    loss = -delta.where(delta < 0, 0)

    avg_gain = gain.rolling(window=period).mean()
    avg_loss = loss.rolling(window=period).mean()

    rs = avg_gain / (avg_loss + 1e-9)  # éviter division par zéro
    rsi = 100 - (100 / (1 + rs))
    return rsi.fillna(50)

def compute_ma(df, period=200):
    return df['close'].rolling(window=period).mean().fillna(method='bfill')

def compute_atr(df, period=14):
    high = df['high']
    low = df['low']
    close = df['close']

    tr1 = high - low
    tr2 = (high - close.shift(1)).abs()
    tr3 = (low - close.shift(1)).abs()
    true_range = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)

    atr = true_range.rolling(window=period).mean()
    return atr.fillna(method='bfill')

def compute_fvg_zones(df):
    """
    Détecte les gaps de valeur (Fair Value Gaps) sur les 3 dernières bougies.
    """
    fvg_upper = []
    fvg_lower = []

    for i in range(2, len(df)):
        prev_low = df['low'].iloc[i - 2]
        mid_close = df['close'].iloc[i - 1]
        curr_high = df['high'].iloc[i]

        prev_high = df['high'].iloc[i - 2]
        curr_low = df['low'].iloc[i]

        # FVG haussier : la bougie du milieu ne remplit pas complètement
        if curr_low > prev_high:
            fvg_upper.append(curr_low)
            fvg_lower.append(prev_high)
        # FVG baissier : même logique inversée
        elif curr_high < prev_low:
            fvg_upper.append(prev_low)
            fvg_lower.append(curr_high)
        else:
            fvg_upper.append(None)
            fvg_lower.append(None)

    # Compléter le début pour correspondre à la longueur
    padding = [None, None]
    fvg_upper = padding + fvg_upper
    fvg_lower = padding + fvg_lower

    return pd.DataFrame({'fvg_upper': fvg_upper, 'fvg_lower': fvg_lower}, index=df.index)
