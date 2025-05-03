import pandas_ta as ta
import numpy as np
import math
import logging

logger = logging.getLogger(__name__)

def _format_price(price: float) -> str:
    """
    Formatte le prix pour afficher toujours les décimales significatives.
    """
    if price == 0:
        return "0"
    exp = math.floor(math.log10(price))
    decimals = 4 if exp >= -4 else abs(exp) + 2
    return f"{price:.{decimals}f}"

def detect_fvg(df) -> bool:
    """
    Détecte un Fair Value Gap bullish sur H4 :
    cherche une bougie i où low[i+1] > high[i].
    """
    for i in range(len(df) - 1):
        if df["low"].iloc[i+1] > df["high"].iloc[i]:
            return True
    return False

def analyze_market(symbol: str, df) -> dict | None:
    """
    Renvoie {symbol, entry, sl, tp} si un signal LONG est détecté,
    sinon None.
    Critères :
      1) FVG bullish présent
      2) RSI entre 40 et 60
      3) MACD quasi-croisement (tolérance 0.002)
      4) Volume ≥ 1.1 × moyenne 20 barres
      5) Dernière bougie haussière
      6) Pullback Fib 61.8 % pour entrée
      7) SL au low, TP à 2× distance SL→entry
    """
    # 1) Au moins un FVG bullish
    if not detect_fvg(df):
        logger.info(f"{symbol} → pas de FVG détecté")
        return None

    # 2) Calcul des indicateurs
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

    # 3) RSI 40–60
    if last_rsi < 40 or last_rsi > 60:
        return None

    # 4) MACD quasi-croisement (MACD >= signal − 0.002)
    if last_macd < last_signal - 0.002:
        return None

    # 5) Volume : dernier bar ≥ 1.1 × volume moyen
    if last_vol < 1.1 * avg_vol:
        return None

    # 6) Bougie haussière
    if last_close <= last_open:
        return None

    # 7) Pullback Fib 61.8 %
    recent_low  = df["low"].iloc[-20:-1].min()
    recent_high = df["high"].iloc[-20:-1].max()
    entry_raw   = recent_low + (recent_high - recent_low) * 0.618
    sl_raw      = recent_low
    tp_raw      = entry_raw + (entry_raw - sl_raw) * 2

    # 8) Formatage des prix
    entry = _format_price(entry_raw)
    sl    = _format_price(sl_raw)
    tp    = _format_price(tp_raw)

    return {
        "symbol": symbol,
        "entry": entry,
        "sl":    sl,
        "tp":    tp
    }
