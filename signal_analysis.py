import pandas_ta as ta
import math

def _format_price(price: float) -> str:
    if price == 0:
        return "0"
    exp = math.floor(math.log10(price))
    decimals = 4 if exp >= -4 else abs(exp) + 2
    return f"{price:.{decimals}f}"

def analyze_market(symbol: str, df) -> dict | None:
    """
    Détecte un signal LONG ou SHORT sur un timeframe H4.
    Renvoie dict {
        side: "LONG" | "SHORT",
        symbol, entry, sl, tp
    } ou None si aucun signal.
    Critères :
      • RSI 40–60
      • MACD quasi-croisement (tolérance ±0.002)
      • Volume ≥ 1.1× moyenne 20 barres
      • Bougie haussière (LONG) ou baissière (SHORT)
      • Pullback Fib 61,8 % pour entrée
      • SL au swing, TP à 2× distance SL→entry
    """
    # 1) RSI + MACD + Signal
    df["rsi"]    = ta.rsi(df["close"], length=14)
    macd_vals    = ta.macd(df["close"])
    df["macd"]   = macd_vals["MACD_12_26_9"]
    df["signal"] = macd_vals["MACDs_12_26_9"]

    last_rsi    = df["rsi"].iloc[-1]
    last_macd   = df["macd"].iloc[-1]
    last_sig    = df["signal"].iloc[-1]
    last_vol    = df["volume"].iloc[-1]
    avg_vol     = df["volume"].iloc[-21:-1].mean()
    last_open   = df["open"].iloc[-1]
    last_close  = df["close"].iloc[-1]

    # 2) RSI 40–60
    if last_rsi < 40 or last_rsi > 60:
        return None

    # 3) MACD quasi-croisement : |MACD − signal| ≤ 0.002
    if abs(last_macd - last_sig) > 0.002:
        return None

    # 4) Volume ≥ 1.1× moyenne
    if last_vol < 1.1 * avg_vol:
        return None

    # 5) Détermination du swing H4
    swing_low   = df["low"].iloc[-20:-1].min()
    swing_high  = df["high"].iloc[-20:-1].max()
    diff        = swing_high - swing_low

    # 6) Cas LONG
    if last_close > last_open:
        entry_raw = swing_low + diff * 0.618
        sl_raw    = swing_low
        tp_raw    = entry_raw + (entry_raw - sl_raw) * 2
        side      = "LONG"

    # 7) Cas SHORT
    elif last_close < last_open:
        entry_raw = swing_high - diff * 0.618
        sl_raw    = swing_high
        tp_raw    = entry_raw - (sl_raw - entry_raw) * 2
        side      = "SHORT"

    # 8) Ni bullish ni bearish
    else:
        return None

    # 9) Formatage des prix
    entry = _format_price(entry_raw)
    sl    = _format_price(sl_raw)
    tp    = _format_price(tp_raw)

    return {
        "side":   side,
        "symbol": symbol,
        "entry":  entry,
        "sl":     sl,
        "tp":     tp
    }
