# signal_analysis.py

import pandas_ta as ta

def is_in_OTE_zone(entry_price, low, high):
    """
    Renvoie (bool, fib618, fib786) pour savoir si entry_price est entre 61.8% et 78.6% du swing.
    """
    fib618 = low + 0.618 * (high - low)
    fib786 = low + 0.786 * (high - low)
    return (fib786 <= entry_price <= fib618), fib618, fib786

def detect_fvg(df):
    """
    Retourne la liste des zones FVG (haussières) sous forme de tuples (high_prev2, low_curr)
    """
    zones = []
    for i in range(2, len(df)):
        h2 = df['high'].iat[i-2]
        l0 = df['low'].iat[i]
        if h2 < l0:
            zones.append((h2, l0))
    return zones

def analyze_market(symbol, df):
    """
    Analyse le marché pour :
     - RSI (14)
     - MACD (12,26,9)
     - Zone OTE (61.8–78.6%)
     - Zone FVG
    Retourne un dict { symbol, entry, sl, tp, ote_zone, fvg_zone, active } ou None.
    """
    # 1) Calcul des indicateurs
    rsi    = ta.rsi(df['close'], length=14)
    macd   = ta.macd(df['close'])
    if rsi is None or macd is None:
        return None
    last_rsi    = rsi.iat[-1]
    last_macd   = macd['MACD_12_26_9'].iat[-1]
    last_signal = macd['MACDs_12_26_9'].iat[-1]

    # Filtre RSI
    if not (40 <= last_rsi <= 60):
        return None
    # Filtre MACD (proche du signal)
    if last_macd < last_signal - 0.001:
        return None

    # 2) Calcul du dernier swing (20 bougies avant la dernière)
    swing_low  = df['low'].iloc[-21:-1].min()
    swing_high = df['high'].iloc[-21:-1].max()

    # === CORRECTION ICI ===
    # Entrée placée au niveau Fibo 61.8% (pour toujours tomber dans la zone OTE)
    entry = swing_low + 0.618 * (swing_high - swing_low)
    # (ou, pour milieu de zone OTE, remplacer 0.618 par (0.618+0.786)/2)

    # 3) Vérification Zone OTE
    ote_ok, fib618, fib786 = is_in_OTE_zone(entry, swing_low, swing_high)
    if not ote_ok:
        return None

    # 4) Vérification Zone FVG
    fvg_zones = detect_fvg(df)
    # On prend la première zone FVG qui contient entry, sinon None
    matching = next(((l,h) for l,h in fvg_zones if l <= entry <= h), None)
    if matching is None:
        return None
    fvg_low, fvg_high = matching

    # 5) Calcul SL / TP (RR 1:2)
    sl = swing_low
    tp = entry + (entry - sl) * 2

    return {
        'symbol':    symbol,
        'entry':     round(entry, 6),
        'sl':        round(sl,    6),
        'tp':        round(tp,    6),
        'ote_zone':  (round(fib786, 6), round(fib618, 6)),
        'fvg_zone':  (round(fvg_low,  6), round(fvg_high, 6)),
        'active':    False
    }
