# signal_analysis.py

import pandas_ta as ta

def is_in_OTE_zone(entry_price, low, high):
    """
    Renvoie (bool, fib618, fib786) si entry_price est entre 61.8% et 78.6% du swing.
    """
    fib618 = low + 0.618 * (high - low)
    fib786 = low + 0.786 * (high - low)
    return (fib618 <= entry_price <= fib786), fib618, fib786

def detect_fvg(df):
    """
    Retourne la liste des zones FVG (haussières) sous forme de tuples (high_prev2, low_curr).
    """
    zones = []
    for i in range(2, len(df)):
        h2 = df['high'].iat[i - 2]
        l0 = df['low'].iat[i]
        if h2 < l0:
            zones.append((h2, l0))
    return zones

def analyze_market(symbol, df):
    """
    Analyse le marché pour RSI(14), MACD(12,26,9), zone OTE et zone FVG.
    Retourne dict {symbol, entry, sl, tp, ote_zone, fvg_zone, active} ou None.
    """
    # 1) indicateurs
    rsi    = ta.rsi(df['close'], length=14)
    macd   = ta.macd(df['close'])
    if rsi is None or macd is None:
        return None
    last_rsi    = rsi.iat[-1]
    last_macd   = macd['MACD_12_26_9'].iat[-1]
    last_signal = macd['MACDs_12_26_9'].iat[-1]

    # Filtre RSI étendu : 30 ≤ RSI ≤ 70
    if last_rsi < 30 or last_rsi > 70:
        return None
    # Filtre MACD : MACD ≥ signal – 0.001
    if last_macd < last_signal - 0.001:
        return None

    # 2) swing (20 bougies avant la dernière)
    swing_low  = df['low'].iloc[-21:-1].min()
    swing_high = df['high'].iloc[-21:-1].max()

    # 3) entrée au Fibo 61.8%
    entry = swing_low + 0.618 * (swing_high - swing_low)

    # 4) zone OTE
    ote_ok, fib618, fib786 = is_in_OTE_zone(entry, swing_low, swing_high)
    if not ote_ok:
        return None

    # 5) zone FVG
    fvg_zones = detect_fvg(df)
    matching = next(((l,h) for l,h in fvg_zones if l <= entry <= h), None)
    if matching is None:
        return None
    fvg_low, fvg_high = matching

    # 6) SL / TP (RR 1:2)
    sl = swing_low
    tp = entry + (entry - sl) * 2

    return {
        'symbol':   symbol,
        'entry':    round(entry,    6),
        'sl':       round(sl,       6),
        'tp':       round(tp,       6),
        'ote_zone': (round(fib618,  6), round(fib786,  6)),
        'fvg_zone': (round(fvg_low, 6), round(fvg_high, 6)),
        'active':   False
    }
