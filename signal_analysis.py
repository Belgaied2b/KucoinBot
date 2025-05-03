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

    if df["rsi"].iloc[-1] < 40 or df["rsi"].iloc[-1] > 60:
        return None

    if df["macd"].iloc[-1] < df["signal"].iloc[-1]:
        return None

    price = df["close"].iloc[-1]
    sl = round(df["low"].iloc[-20:-1].min(), 4)
    tp = round(price + (price - sl) * 2, 4)

    msg = (
        f"📈 {symbol}\n"
        f"RSI: {df['rsi'].iloc[-1]:.2f}\n"
        f"MACD: {df['macd'].iloc[-1]:.4f} > {df['signal'].iloc[-1]:.4f}\n"
        f"Entrée: {price:.4f}\n"
        f"TP: {tp:.4f} | SL: {sl:.4f}"
    )
    return msg
