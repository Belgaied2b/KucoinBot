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

def compute_ote(df, direction="long", tolerance=0.020):
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

    except Exception:
        return {
            "in_ote": False,
            "entry": df['close'].iloc[-1],
            "zone": (None, None),
            "price": df['close'].iloc[-1]
        }

def compute_fvg(df, direction="long"):
    """
    FVG détecté sur 3 à 10 dernières bougies.
    - LONG : low actuelle > high -i
    - SHORT : high actuelle < low -i
    """
    try:
        if len(df) < 10:
            return {"valid": False, "sl": None, "zone": None}

        for i in range(3, 11):
            h_back = df['high'].iloc[-i]
            l_back = df['low'].iloc[-i]
            h_now = df['high'].iloc[-1]
            l_now = df['low'].iloc[-1]

            if direction == "long" and l_now > h_back:
                return {
                    "valid": True,
                    "sl": l_back,
                    "zone": (h_back, l_now)
                }
            elif direction == "short" and h_now < l_back:
                return {
                    "valid": True,
                    "sl": h_back,
                    "zone": (h_now, l_back)
                }

        return {"valid": False, "sl": None, "zone": None}

    except:
        return {"valid": False, "sl": None, "zone": None}
