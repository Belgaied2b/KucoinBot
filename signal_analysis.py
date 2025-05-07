# signal_analysis.py

import pandas_ta as ta
import logging
logger = logging.getLogger(__name__)

def is_in_OTE_zone(entry_price, low, high):
    fib618 = low + 0.618 * (high - low)
    fib786 = low + 0.786 * (high - low)
    return (fib618 <= entry_price <= fib786), fib618, fib786

def detect_fvg(df):
    zones = []
    for i in range(2, len(df)):
        h2 = df['high'].iat[i - 2]
        l0 = df['low'].iat[i]
        if h2 < l0:
            zones.append((h2, l0))
    return zones

def analyze_market(symbol, df):
    rsi = ta.rsi(df['close'], length=14)
    if rsi is None or rsi.isna().all():
        logger.info(f"{symbol} [4H] rejet: RSI non calculable")
        return None

    macd = ta.macd(df['close'])
    if macd is None or macd.isna().all():
        logger.info(f"{symbol} [4H] rejet: MACD non calculable")
        return None

    last_rsi    = rsi.iat[-1]
    last_macd   = macd['MACD_12_26_9'].iat[-1]
    last_signal = macd['MACDs_12_26_9'].iat[-1]

    if last_rsi < 40 or last_rsi > 60:
        logger.info(f"{symbol} [4H] rejet RSI={last_rsi:.1f}")
        return None
    if last_macd < last_signal:
        logger.info(f"{symbol} [4H] rejet MACD={last_macd:.4f} < signal={last_signal:.4f}")
        return None

    high = df['high'].rolling(20).max().iat[-2]
    low  = df['low'].rolling(20).min().iat[-2]
    price = df['close'].iat[-1]

    in_ote, fib618, fib786 = is_in_OTE_zone(price, low, high)
    if not in_ote:
        logger.info(f"{symbol} [4H] rejet OTE: price={price} hors zone [{fib618:.4f}-{fib786:.4f}]")
        return None

    fvg_zones = detect_fvg(df)
    if not fvg_zones:
        logger.info(f"{symbol} [4H] rejet: aucun FVG détecté")
        return None

    return {
        "symbol": symbol,
        "entry": round(fib618, 5),
        "sl": round(low * 0.99, 5),
        "tp": round(fib786 * 1.02, 5),
        "ote_zone": (round(fib618, 5), round(fib786, 5)),
        "fvg_zone": fvg_zones[-1],
        "active": True
    }
