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

    high = float(df['high'].rolling(20).max().iat[-2])
    low  = float(df['low'].rolling(20).min().iat[-2])
    price = float(df['close'].iat[-1])

    in_ote, fib618, fib786 = is_in_OTE_zone(price, low, high)
    if not in_ote:
        logger.info(f"{symbol} [4H] rejet OTE: price={price:.2f} ∉ [{fib618:.2f}, {fib786:.2f}]")
        return None

    fvg_zones = detect_fvg(df)
    valid_zones = [(fvg_low, fvg_high) for fvg_low, fvg_high in fvg_zones if fvg_low <= price <= fvg_high]
    if not valid_zones:
        logger.info(f"{symbol} [4H] rejet: aucun FVG actif")
        return None

    logger.info(f"{symbol} [4H] ✅ Signal détecté")
    return {
        'symbol': symbol,
        'rsi': last_rsi,
        'macd': last_macd,
        'macd_signal': last_signal,
        'ote_zone': (fib618, fib786),
        'fvg_zone': valid_zones[-1],
        'price': price,
        'direction': 'long'
    }
