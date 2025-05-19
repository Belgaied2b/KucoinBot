import pandas as pd

def compute_rsi(series, period=14):
    delta = series.diff()
    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)
    avg_gain = gain.rolling(window=period).mean()
    avg_loss = loss.rolling(window=period).mean()
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
    high = df["high"]
    low = df["low"]
    close = df["close"]
    tr = pd.concat([
        high - low,
        (high - close.shift()).abs(),
        (low - close.shift()).abs()
    ], axis=1).max(axis=1)
    atr = tr.rolling(window=period).mean()
    return atr

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
            in_ote = min_ote <= df['close'].iloc[-1] <= max_ote
        else:
            fib618 = high - 0.618 * (high - low)
            fib786 = high - 0.786 * (high - low)
            min_ote = fib618 * (1 - tolerance)
            max_ote = fib786 * (1 + tolerance)
            in_ote = max_ote <= df['close'].iloc[-1] <= min_ote

        entry = (fib618 + fib786) / 2
        return {
            "in_ote": in_ote,
            "entry": entry,
            "zone": (round(min_ote, 6), round(max_ote, 6)),
            "price": df['close'].iloc[-1]
        }

    except Exception as e:
        return {
            "in_ote": False,
            "entry": df['close'].iloc[-1],
            "zone": (None, None),
            "price": df['close'].iloc[-1]
        }

def compute_fvg(df, direction="long"):
    try:
        if len(df) < 3:
            return {"valid": False, "sl": None, "zone": None}

        h2 = df['high'].iloc[-3]
        l0 = df['low'].iloc[-1]
        l2 = df['low'].iloc[-3]
        h0 = df['high'].iloc[-1]

        if direction == "long" and l0 > h2:
            return {
                "valid": True,
                "sl": l2,
                "zone": (h2, l0)
            }
        elif direction == "short" and h0 < l2:
            return {
                "valid": True,
                "sl": h2,
                "zone": (h0, l2)
            }

        return {"valid": False, "sl": None, "zone": None}
    except:
        return {"valid": False, "sl": None, "zone": None}
