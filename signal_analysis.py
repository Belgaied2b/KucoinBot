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
    retourne True si pour une bougie i, low[i+1] > high[i].
    """
    for i in range(len(df) - 1):
        if df["low"].iloc[i+1] > df["high"].iloc[i]:
            return True
    return False

def analyze_market(symbol: str, df) -> dict | None:
    """
    Renvoie un dict {symbol, entry, sl, tp, ote_zone, has_fvg} si un signal LONG
    est détecté (selon RSI/MACD/volume/bougie/calc Fib), sinon None.
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

    # 2) RSI 40–60
    if last_rsi < 40 or last_rsi > 60:
        return None

    # 3) MACD quasi-croisement (MACD >= signal - 0.002)
    if last_macd < last_signal - 0.002:
        return None

    # 4) Volume ≥ 1.1× volume moyen
    if last_vol < 1.1 * avg_vol:
        return None

    # 5) Dernière bougie haussière
    if last_close <= last_open:
        return None

    # 6) Pullback Fib 61.8 %
    recent_low  = df["low"].iloc[-20:-1].min()
    recent_high = df["high"].iloc[-20:-1].max()
    entry_raw   = recent_low + (recent_high - recent_low) * 0.618
    sl_raw      = recent_low
    tp_raw      = entry_raw + (entry_raw - sl_raw) * 2

    # 7) Calcul de la zone OTE (61.8–78.6 %)
    ote_low  = recent_low + (recent_high - recent_low) * 0.618
    ote_high = recent_low + (recent_high - recent_low) * 0.786

    # 8) Détection FVG pour annotation
    has_fvg = detect_fvg(df)
    if not has_fvg:
        logger.info(f"{symbol} → pas de FVG détecté")

    # 9) Formatage des prix
    entry = _format_price(entry_raw)
    sl    = _format_price(sl_raw)
    tp    = _format_price(tp_raw)

    return {
        "symbol":   symbol,
        "entry":    entry,
        "sl":       sl,
        "tp":       tp,
        "ote_zone": (ote_low, ote_high),
        "has_fvg":  has_fvg
    }
