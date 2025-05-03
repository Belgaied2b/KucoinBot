# signal_analysis.py

import pandas as pd
import pandas_ta as ta

def analyze_market(symbol, df):
    if df is None or len(df) < 50:
        return None

    df["rsi"] = ta.rsi(df["close"], length=14)
    macd = ta.macd(df["close"])
    if macd is None or "MACD_12_26_9" not in macd or "MACDs_12_26_9" not in macd:
        return None
    df["macd"] = macd["MACD_12_26_9"]
    df["macd_signal"] = macd["MACDs_12_26_9"]

    rsi = df["rsi"].iloc[-1]
    macd_val = df["macd"].iloc[-1]
    macd_signal = df["macd_signal"].iloc[-1]
    close = df["close"].iloc[-1]

    # Fibo (niveau 0.236, 0.382, 0.5)
    recent_lows = df["low"].rolling(window=20).min()
    recent_highs = df["high"].rolling(window=20).max()
    low = recent_lows.iloc[-1]
    high = recent_highs.iloc[-1]
    fibo_236 = low + 0.236 * (high - low)
    fibo_382 = low + 0.382 * (high - low)
    fibo_500 = low + 0.5 * (high - low)

    # Conditions de signal LONG
    if (
        40 < rsi < 60 and
        macd_val > macd_signal and
        close >= fibo_382 and close <= fibo_500
    ):
        sl = round(low * 0.995, 4)  # SL sous support
        tp = round(close * 1.03, 4)
        entry = round(close * 0.9975, 4)
        return {
            "symbol": symbol,
            "side": "LONG",
            "entry": entry,
            "sl": sl,
            "tp": tp,
            "message": f"🚀 LONG {symbol}\n🎯 Entrée: {entry}\n📉 SL: {sl}\n📈 TP: {tp}"
        }

    # Conditions de signal SHORT
    if (
        40 < rsi < 60 and
        macd_val < macd_signal and
        close <= fibo_382 and close >= fibo_236
    ):
        sl = round(high * 1.005, 4)
        tp = round(close * 0.97, 4)
        entry = round(close * 1.0025, 4)
        return {
            "symbol": symbol,
            "side": "SHORT",
            "entry": entry,
            "sl": sl,
            "tp": tp,
            "message": f"🔥 SHORT {symbol}\n🎯 Entrée: {entry}\n📉 SL: {sl}\n📈 TP: {tp}"
        }

    return None
