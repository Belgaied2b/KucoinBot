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

def compute_fvg(df):
    """
    Détecte un Fair Value Gap (FVG) sur les 3 dernières bougies.
    Renvoie un FVG valide si une bougie laisse un 'trou' entre le corps et l'ombre précédente.
    """
    try:
        last = df.iloc[-1]
        prev = df.iloc[-2]
        before_prev = df.iloc[-3]

        # FVG haussier : low actuel > high 2 bougies avant
        if last['low'] > before_prev['high']:
            sl = before_prev['low']
            return {"valid": True, "sl": sl}

        # FVG baissier : high actuel < low 2 bougies avant
        if last['high'] < before_prev['low']:
            sl = before_prev['high']
            return {"valid": True, "sl": sl}

        return {"valid": False, "sl": None}
    except:
        return {"valid": False, "sl": None}

def compute_ote(df, direction="long"):
    """
    Calcule la zone OTE (Optimal Trade Entry) :
    - zone entre le retracement 0.618 et 0.786 de Fibonacci
    - pour une tendance directionnelle
    """
    try:
        # On prend les 50 dernières bougies pour repérer le swing
        lookback = df[-50:]
        high = lookback['high'].max()
        low = lookback['low'].min()

        if direction == "long":
            fib618 = low + 0.618 * (high - low)
            fib786 = low + 0.786 * (high - low)
            entry_zone = (fib786, fib618)
            entry = (fib786 + fib618) / 2
            price = df['close'].iloc[-1]
            return {
                "in_ote": fib786 <= price <= fib618,
                "entry": entry,
                "zone": entry_zone
            }

        else:  # short
            fib618 = high - 0.618 * (high - low)
            fib786 = high - 0.786 * (high - low)
            entry_zone = (fib618, fib786)
            entry = (fib618 + fib786) / 2
            price = df['close'].iloc[-1]
            return {
                "in_ote": fib618 <= price <= fib786,
                "entry": entry,
                "zone": entry_zone
            }

    except Exception as e:
        print(f"⚠️ Erreur OTE : {e}")
        return {
            "in_ote": False,
            "entry": df['close'].iloc[-1],
            "zone": (None, None)
        }
