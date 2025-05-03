import pandas_ta as ta
import math
import logging

logger = logging.getLogger(__name__)

def _format_price(price: float) -> str:
    if price == 0:
        return "0"
    exp = math.floor(math.log10(price))
    decimals = 4 if exp >= -4 else abs(exp) + 2
    return f"{price:.{decimals}f}"

def analyze_market(symbol: str, df) -> dict | None:
    # Indic­ateurs
    df["rsi"]    = ta.rsi(df["close"], length=14)
    macd        = ta.macd(df["close"])
    df["macd"]   = macd["MACD_12_26_9"]
    df["signal"] = macd["MACDs_12_26_9"]

    last_rsi    = df["rsi"].iloc[-1]
    last_macd   = df["macd"].iloc[-1]
    last_signal = df["signal"].iloc[-1]
    last_vol    = df["volume"].iloc[-1]
    avg_vol     = df["volume"].iloc[-21:-1].mean()
    last_open   = df["open"].iloc[-1]
    last_close  = df["close"].iloc[-1]

    # Log debug
    logger.info(
        f"{symbol} → RSI {last_rsi:.1f}, MACD {last_macd:.4f} vs sig {last_signal:.4f}, "
        f"Vol {last_vol:.0f}/{avg_vol:.0f}, Candle {'↑' if last_close>last_open else '↓'}"
    )

    # 1) RSI 35–65 (assoupli)
    if last_rsi < 35 or last_rsi > 65:
        return None

    # 2) MACD quasi-croisement (tolérance 0.005)
    if last_macd < last_signal - 0.005:
        return None

    # 3) Volume ≥ 1.05× moyenne
    if last_vol < 1.05 * avg_vol:
        return None

    # 4) Bougie haussière
    if last_close <= last_open:
        return None

    # 5) Pullback Fib 61.8 %
    recent_low  = df["low"].iloc[-20:-1].min()
    recent_high = df["high"].iloc[-20:-1].max()
    entry_raw   = recent_low + (recent_high - recent_low) * 0.618
    sl_raw      = recent_low
    tp_raw      = entry_raw + (entry_raw - sl_raw) * 2

    entry = _format_price(entry_raw)
    sl    = _format_price(sl_raw)
    tp    = _format_price(tp_raw)

    return {"symbol": symbol, "entry": entry, "sl": sl, "tp": tp}
