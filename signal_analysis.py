import pandas_ta as ta
import numpy as np

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

    # ✅ RSI élargi : 40–60
    if last_rsi < 40 or last_rsi > 60:
        return None

    # ✅ MACD : autorisé si proche du signal (pré-croisement)
    if last_macd < last_signal - 0.001:
        return None

    # ✅ Fibo : on utilise directement le niveau 50% (entrée plus fréquente)
    recent_low = df["low"].iloc[-20:-1].min()
    recent_high = df["high"].iloc[-20:-1].max()
    fib_entry = recent_low + (recent_high - recent_low) * 0.5
    entry = round(fib_entry, 4)

    # SL / TP pro
    sl = round(recent_low, 4)
    tp = round(entry + (entry - sl) * 2, 4)

    return {
        "symbol": symbol,
        "entry": entry,
        "tp": tp,
        "sl": sl
    }
