import pandas as pd

def compute_rsi(series, period=14):
    delta = series.diff()
    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)
    avg_gain = gain.ewm(alpha=1/period, min_periods=period).mean()
    avg_loss = loss.ewm(alpha=1/period, min_periods=period).mean()
    rs = avg_gain / avg_loss
    rsi = 100 - (100 / (1 + rs))
    return rsi

def compute_macd(series, fast=12, slow=26, signal=9):
    ema_fast = series.ewm(span=fast, adjust=False).mean()
    ema_slow = series.ewm(span=slow, adjust=False).mean()
    macd_line = ema_fast - ema_slow
    signal_line = macd_line.ewm(span=signal, adjust=False).mean()
    return macd_line, signal_line

def compute_atr(df, period=14):
    high_low = df['high'] - df['low']
    high_close = (df['high'] - df['close'].shift()).abs()
    low_close = (df['low'] - df['close'].shift()).abs()
    tr = pd.concat([high_low, high_close, low_close], axis=1).max(axis=1)
    atr = tr.rolling(window=period).mean()
    return atr

def compute_fvg(df, direction="long"):
    try:
        last = df.iloc[-1]
        before_prev = df.iloc[-3]

        if direction == "long":
            # FVG haussier = prix saute au-dessus du précédent
            if last['low'] > before_prev['high']:
                sl = before_prev['low']  # SL sous le FVG
                return {"valid": True, "sl": sl}
        else:
            # FVG baissier = prix saute sous le précédent
            if last['high'] < before_prev['low']:
                sl = before_prev['high']  # SL au-dessus du FVG
                return {"valid": True, "sl": sl}

        return {"valid": False, "sl": None}
    except Exception as e:
        print(f"⚠️ Erreur FVG : {e}")
        return {"valid": False, "sl": None}

def compute_ote(df, direction="long", tolerance=0.015):
    try:
        lookback = df[-50:]
        high = lookback['high'].max()
        low = lookback['low'].min()

        if direction == "long":
            fib618 = low + 0.618 * (high - low)
            fib786 = low + 0.786 * (high - low)
            min_ote = fib786 * (1 - tolerance)
            max_ote = fib618 * (1 + tolerance)
        else:
            fib618 = high - 0.618 * (high - low)
            fib786 = high - 0.786 * (high - low)
            min_ote = fib618 * (1 - tolerance)
            max_ote = fib786 * (1 + tolerance)

        entry = (fib618 + fib786) / 2
        price = df['close'].iloc[-1]

        return {
            "in_ote": min_ote <= price <= max_ote,
            "entry": entry,
            "zone": (round(min_ote, 6), round(max_ote, 6)),
            "price": price
        }

    except Exception as e:
        print(f"⚠️ Erreur OTE : {e}")
        return {
            "in_ote": False,
            "entry": df['close'].iloc[-1],
            "zone": (None, None),
            "price": df['close'].iloc[-1]
        }

def is_cos_valid(df):
    if len(df) < 50:
        return False
    recent_zone = df[-20:]
    previous_zone = df[-40:-20]
    prev_high = previous_zone['high'].max()
    last_high = recent_zone['high'].iloc[-1]
    return last_high > prev_high

def is_bos_valid(df):
    recent_high = df['high'].iloc[-5:-1].max()
    current_close = df['close'].iloc[-1]
    return current_close > recent_high
