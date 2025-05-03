import pandas_ta as ta
import numpy as np
import math

def _format_price(price: float) -> str:
    """
    Formatte le prix pour afficher toujours les décimales significatives :
      - >= 1e-4 : 4 décimales
      - <  1e-4 : abs(exposant) + 2 décimales
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
    """
    Renvoie un dict {symbol, entry, sl, tp} si un signal LONG est détecté,
    sinon None. Conditions :
      1) RSI 42–58
      2) MACD quasi-croisement (tolérance 0.002)
      3) Volume bar ≥ 1.1× moyenne 20 barres
      4) Dernière bougie haussière
      5) Entrée sur retracement Fib 61.8 %
      6) Entrée doit tomber dans la zone OTE 61.8–78.6 %
      7) SL = plus bas, TP = 2× distance SL→entry
    """
    # 1) Calcul RSI & MACD
    df["rsi"]    = ta.rsi(df["close"], length=14)
    macd_vals    = ta.macd(df["close"])
    df["macd"]   = macd_vals["MACD_12_26_9"]
    df["signal"] = macd_vals["MACDs_12_26_9"]

    last_rsi    = df["rsi"].iloc[-1]
    last_macd   = df["macd"].iloc[-1]
    last_signal = df["signal"].iloc[-1]
    last_vol    = df["volume"].iloc[-1]
    avg_vol     = df["volume"].iloc[-21:-1].mean()
    last_open   = df["open"].iloc[-1]
    last_close  = df["close"].iloc[-1]

    # 1) RSI assoupli : 42–58
    if last_rsi < 42 or last_rsi > 58:
        return None

    # 2) MACD quasi-croisement : MACD >= signal - 0.002
    if last_macd < last_signal - 0.002:
        return None

    # 3) Volume : dernier bar ≥ 1.1 × moyenne
    if last_vol < 1.1 * avg_vol:
        return None

    # 4) Dernière bougie haussière
    if last_close <= last_open:
        return None

    # 5) Calcul du retracement Fib 61.8 %
    recent_low  = df["low"].iloc[-20:-1].min()
    recent_high = df["high"].iloc[-20:-1].max()
    entry_raw   = recent_low + (recent_high - recent_low) * 0.618
    sl_raw      = recent_low
    tp_raw      = entry_raw + (entry_raw - sl_raw) * 2

    # 6) Filtre OTE : 61.8–78.6 % de Fib
    ote_low  = recent_low + (recent_high - recent_low) * 0.618
    ote_high = recent_low + (recent_high - recent_low) * 0.786
    if not (ote_low <= entry_raw <= ote_high):
        return None

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
