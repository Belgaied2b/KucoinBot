# signal_analysis.py

import pandas_ta as ta

def is_in_OTE_zone(entry_price, low, high):
    fib_618 = low + 0.618 * (high - low)
    fib_786 = low + 0.786 * (high - low)
    return fib_786 <= entry_price <= fib_618

def detect_fvg(df):
    fvg_zones = []
    for i in range(2, len(df)):
        high_prev2 = df['high'].iat[i - 2]
        low_curr   = df['low'].iat[i]
        if high_prev2 < low_curr:
            fvg_zones.append((high_prev2, low_curr))
    return fvg_zones

def analyze_market(symbol, df):
    # Calcul des indicateurs
    rsi  = ta.rsi(df['close'], length=14)
    macd = ta.macd(df['close'])
    if rsi is None or macd is None:
        return None

    df['rsi']    = rsi
    df['macd']   = macd['MACD_12_26_9']
    df['signal'] = macd['MACDs_12_26_9']

    # Conditions RSI / MACD
    last_rsi    = df['rsi'].iat[-1]
    last_macd   = df['macd'].iat[-1]
    last_signal = df['signal'].iat[-1]
    if last_rsi < 40 or last_rsi > 60:
        return None
    if last_macd < last_signal - 0.001:
        return None

    # Détermination du dernier swing pour tracer Fibo
    swing_low  = df['low'].iloc[-21:-1].min()
    swing_high = df['high'].iloc[-21:-1].max()
    entry      = round(swing_low + 0.5 * (swing_high - swing_low), 6)

    # Filtre OTE
    if not is_in_OTE_zone(entry, swing_low, swing_high):
        return None

    # Filtre FVG
    fvg_zones = detect_fvg(df)
    matching  = [z for z in fvg_zones if z[0] <= entry <= z[1]]
    if not matching:
        return None

    # Calcul SL / TP
    sl = round(swing_low, 6)
    tp = round(entry + (entry - sl) * 2, 6)

    ote_zone = (
        round(swing_low + 0.786 * (swing_high - swing_low), 6),
        round(swing_low + 0.618 * (swing_high - swing_low), 6)
    )

    return {
        'symbol':    symbol,
        'entry':     entry,
        'sl':        sl,
        'tp':        tp,
        'ote_zone':  ote_zone,
        'fvg_zone':  matching[0],
        'active':    False
    }
