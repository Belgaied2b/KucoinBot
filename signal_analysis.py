import pandas_ta as ta
import numpy as np
import math

def _format_price(price: float) -> str:
    """
    Formatte le prix avec un nombre dynamique de décimales :
      - >=1e-4 : 4 décimales
      - <1e-4 : abs(exposant)+2 décimales (pour afficher les chiffres significatifs)
    """
    if price == 0:
        return "0"
    exp = math.floor(math.log10(price))
    if exp >= -4:
        decimals = 4
    else:
        decimals = abs(exp) + 2
    return f"{price:.{decimals}f}"

def analyze_market(symbol: str, df) -> dict | None:
    rsi = ta.rsi(df["close"], length=14)
    macd = ta.macd(df["close"])
    if rsi is None or macd is None:
        return None

    df["rsi"]    = rsi
    df["macd"]   = macd["MACD_12_26_9"]
    df["signal"] = macd["MACDs_12_26_9"]

    last_rsi    = df["rsi"].iloc[-1]
    last_macd   = df["macd"].iloc[-1]
    last_signal = df["signal"].iloc[-1]

    # RSI élargi 40–60
    if last_rsi < 40 or last_rsi > 60:
        return None
    # MACD pré-croisement toléré
    if last_macd < last_signal - 0.001:
        return None

    # Pullback Fibo 50 %
    recent_low  = df["low"].iloc[-20:-1].min()
    recent_high = df["high"].iloc[-20:-1].max()
    entry_raw   = recent_low + (recent_high - recent_low) * 0.5
    sl_raw      = recent_low
    tp_raw      = entry_raw + (entry_raw - sl_raw) * 2

    # Formattage dynamique
    entry = _format_price(entry_raw)
    sl    = _format_price(sl_raw)
    tp    = _format_price(tp_raw)

    return {
        "symbol": symbol,
        "entry": entry,
        "sl":    sl,
        "tp":    tp
    }
