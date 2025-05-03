import pandas_ta as ta
import numpy as np

def analyze_market(symbol, df):
    # Indicateurs
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

    # ✅ RSI élargi : 42–58 (zone de neutralité élargie)
    if last_rsi < 42 or last_rsi > 58:
        return None

    # ✅ MACD : croisement haussier seulement
    if last_macd < last_signal:
        return None

    # Récupération de la zone de swing
    recent_low = df["low"].iloc[-20:-1].min()
    recent_high = df["high"].iloc[-20:-1].max()

    # ✅ Entrée Fibo 61.8 % ou 50 % (si amplitude trop serrée)
    fib_range = recent_high - recent_low
    if fib_range < 0.01 * recent_high:  # Si trop serré (<1%)
        fib_level = 0.5  # Fallback à 50 %
    else:
        fib_level = 0.618

    fib_entry = recent_low + fib_range * fib_level
    entry = round(fib_entry, 4)

    # TP / SL pro
    sl = round(recent_low, 4)
    tp = round(entry + (entry - sl) * 2, 4)

    return {
        "symbol": symbol,
        "entry": entry,
        "tp": tp,
        "sl": sl
    }
