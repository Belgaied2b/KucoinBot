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

    # ✅ Condition pro : RSI neutre (zone de recharge)
    if last_rsi < 45 or last_rsi > 55:
        return None

    # ✅ MACD haussier (et idéalement sous 0)
    if last_macd < last_signal or last_macd > 0:
        return None

    # Récupération zone de swing
    recent_low = df["low"].iloc[-20:-1].min()
    recent_high = df["high"].iloc[-20:-1].max()

    # ✅ Entrée Fibo 61.8 % (pullback optimal)
    fib_618 = recent_low + (recent_high - recent_low) * 0.618
    entry = round(fib_618, 4)

    # TP et SL pro
    sl = round(recent_low, 4)
    tp = round(entry + (entry - sl) * 2, 4)

    return {
        "symbol": symbol,
        "entry": entry,
        "tp": tp,
        "sl": sl
    }
