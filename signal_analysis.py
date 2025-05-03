import pandas_ta as ta

def is_in_OTE_zone(entry_price, low, high):
    fib_618 = low + 0.618 * (high - low)
    fib_786 = low + 0.786 * (high - low)
    return fib_786 <= entry_price <= fib_618

def detect_fvg(df):
    fvg_zones = []
    for i in range(2, len(df)):
        high2 = df['high'].iloc[i - 2]
        low0 = df['low'].iloc[i]
        if high2 < low0:
            fvg_zones.append((high2, low0))
    return fvg_zones

def analyze_market(symbol, df):
    rsi = ta.rsi(df["close"], length=14)
    macd = ta.macd(df["close"])
    if rsi is None or macd is None:
        return None

    df["rsi"] = rsi
    df["macd"] = macd["MACD_12_26_9"]
    df["signal"] = macd["MACDs_12_26_9"]

    last_rsi = df["rsi"].iloc[-1]
    last_macd = df["macd"].iloc[-1]
    last_signal = df["signal"].iloc[-1]

    if last_rsi < 40 or last_rsi > 60:
        return None
    if last_macd < last_signal - 0.001:
        return None

    recent_low = df["low"].iloc[-21:-1].min()
    recent_high = df["high"].iloc[-21:-1].max()
    entry = round(recent_low + 0.5 * (recent_high - recent_low), 6)

    # Vérifier la zone OTE
    if not is_in_OTE_zone(entry, recent_low, recent_high):
        return None

    # Vérifier s'il y a une FVG couvrant cette entrée
    fvg_zones = detect_fvg(df)
    matching_fvg = [zone for zone in fvg_zones if zone[0] <= entry <= zone[1]]
    if not matching_fvg:
        return None

    sl = round(recent_low, 6)
    tp = round(entry + (entry - sl) * 2, 6)

    return {
        "symbol": symbol,
        "entry": entry,
        "sl": sl,
        "tp": tp,
        "fvg_zone": matching_fvg[0],
        "ote_zone": (round(recent_low + 0.786 * (recent_high - recent_low), 6),
                     round(recent_low + 0.618 * (recent_high - recent_low), 6)),
        "active": False
    }
