import pandas_ta as ta
import numpy as np
import math

def _format_price(price: float) -> str:
    if price == 0:
        return "0"
    exp = math.floor(math.log10(price))
    if exp >= -4:
        decimals = 4
    else:
        decimals = abs(exp) + 2
    return f"{price:.{decimals}f}"

def analyze_market(symbol: str, df) -> dict | None:
    # 1) Calcul des indicateurs
    df["rsi"] = ta.rsi(df["close"], length=14)
    macd_vals = ta.macd(df["close"])
    df["macd"]   = macd_vals["MACD_12_26_9"]
    df["signal"] = macd_vals["MACDs_12_26_9"]

    last_rsi    = df["rsi"].iloc[-1]
    last_macd   = df["macd"].iloc[-1]
    last_signal = df["signal"].iloc[-1]
    last_vol    = df["volume"].iloc[-1]
    avg_vol     = df["volume"].iloc[-21:-1].mean()
    last_open   = df["open"].iloc[-1]
    last_close  = df["close"].iloc[-1]

    # 2) RSI assoupli : 42–58
    if last_rsi < 42 or last_rsi > 58:
        return None

    # 3) MACD : croisement haussier ou quasi-croisement (tolérance 0.002)
    if last_macd < last_signal - 0.002:
        return None

    # 4) Volume : dernier bar ≥ 1.1 × volume moyen
    if last_vol < 1.1 * avg_vol:
        return None

    # 5) On exige une bougie haussière pour confirmer le momentum
    if last_close <= last_open:
        return None

    # 6) Pullback Fib 61.8 %
    recent_low  = df["low"].iloc[-20:-1].min()
    recent_high = df["high"].iloc[-20:-1].max()
    entry_raw   = recent_low + (recent_high - recent_low) * 0.618
    sl_raw      = recent_low
    tp_raw      = entry_raw + (entry_raw - sl_raw) * 2

    # 7) Formatage dynamique des prix
    entry = _format_price(entry_raw)
    sl    = _format_price(sl_raw)
    tp    = _format_price(tp_raw)

    return {
        "symbol": symbol,
        "entry": entry,
        "sl":    sl,
        "tp":    tp
    }
