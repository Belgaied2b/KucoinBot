# signal_analysis.py

import pandas_ta as ta
import numpy as np
import logging

logger = logging.getLogger(__name__)

def analyze_market(symbol, df):
    if df is None or df.empty:
        return None

    df["rsi"] = ta.rsi(df["close"], length=14)
    macd = ta.macd(df["close"])
    df["macd"] = macd["MACD_12_26_9"]
    df["macd_signal"] = macd["MACDs_12_26_9"]

    df.dropna(inplace=True)
    if df.empty or len(df) < 20:
        return None

    last = df.iloc[-1]
    prev = df.iloc[-2]

    # Filtrage swing trading
    if not (40 <= last["rsi"] <= 60):
        return None
    if np.sign(last["macd"] - last["macd_signal"]) != 1:
        return None

    # Détection d’un retracement haussier sur support Fibo
    high = df["high"].rolling(20).max().iloc[-1]
    low = df["low"].rolling(20).min().iloc[-1]
    fibo_levels = [low + 0.5 * (high - low), low + 0.618 * (high - low)]
    fibo_support = fibo_levels[0] <= last["close"] <= fibo_levels[1]

    if not fibo_support:
        return None

    sl = round(df["low"].rolling(20).min().iloc[-1] * 0.995, 4)
    tp = round(last["close"] * 1.05, 4)
    entry_price = round(last["close"] * 0.995, 4)

    logger.info(f"✅ Signal détecté sur {symbol}")

    return {
        "symbol": symbol,
        "entry": entry_price,
        "tp": tp,
        "sl": sl,
        "rsi": round(last["rsi"], 2),
        "macd": round(last["macd"], 5),
        "signal": round(last["macd_signal"], 5),
        "fibo_range": (round(fibo_levels[0], 4), round(fibo_levels[1], 4)),
        "close": round(last["close"], 4),
    }
