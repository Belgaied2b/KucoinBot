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
    # 1) Calcul des indicateurs de base
    df["rsi"]    = ta.rsi(df["close"], length=14)
    macd_vals    = ta.macd(df["close"])
    df["macd"]   = macd_vals["MACD_12_26_9"]
    df["signal"] = macd_vals["MACDs_12_26_9"]
    df["sma50"]  = ta.sma(df["close"], length=50)

    # Valeurs courantes et précédentes
    last_rsi      = df["rsi"].iloc[-1]
    last_macd     = df["macd"].iloc[-1]
    last_signal   = df["signal"].iloc[-1]
    prev_macd     = df["macd"].iloc[-2]
    prev_signal   = df["signal"].iloc[-2]
    last_vol      = df["volume"].iloc[-1]
    avg_vol       = df["volume"].iloc[-21:-1].mean()
    last_close    = df["close"].iloc[-1]
    last_sma50    = df["sma50"].iloc[-1]

    # 2) RSI super strict : 47–53
    if not (47 <= last_rsi <= 53):
        return None

    # 3) MACD : VRAI croisement haussier dans les 2 dernières barres, et MACD toujours sous -0.01
    if not (prev_macd < prev_signal and last_macd > last_signal and last_macd < -0.01):
        return None

    # 4) Volume : dernier bar ≥ 1.5 × volume moyen des 20 précédentes
    if last_vol < 1.5 * avg_vol:
        return None

    # 5) Tendances long terme : on exige que le cours soit au-dessus de sa SMA50
    if last_close < last_sma50:
        return None

    # 6) Formation bullish candle : dernière bougie doit être haussière
    if df["close"].iloc[-1] <= df["open"].iloc[-1]:
        return None

    # 7) Calcul du pullback et entrée Fib 61.8 %
    recent_low   = df["low"].iloc[-20:-1].min()
    recent_high  = df["high"].iloc[-20:-1].max()
    entry_raw    = recent_low + (recent_high - recent_low) * 0.618
    sl_raw       = recent_low
    tp_raw       = entry_raw + (entry_raw - sl_raw) * 2

    # 8) Formatage dynamique des prix
    entry = _format_price(entry_raw)
    sl    = _format_price(sl_raw)
    tp    = _format_price(tp_raw)

    return {
        "symbol": symbol,
        "entry": entry,
        "sl":    sl,
        "tp":    tp
    }
