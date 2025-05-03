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

    # Conditions RSI et MACD haussier
    if df["rsi"].iloc[-1] < 40 or df["rsi"].iloc[-1] > 60:
        return None
    if df["macd"].iloc[-1] < df["signal"].iloc[-1]:
        return None

    # Zone de retracement Fibo entre le plus bas et le plus haut récents
    recent_low = df["low"].iloc[-20:-1].min()
    recent_high = df["high"].iloc[-20:-1].max()
    fib_618 = recent_low + (recent_high - recent_low) * 0.618
    entry = round(fib_618, 4)

    # TP / SL
    sl = round(recent_low, 4)
    tp = round(entry + (entry - sl) * 2, 4)

    return {
        "symbol": symbol,
        "entry": entry,
        "tp": tp,
        "sl": sl
    }
