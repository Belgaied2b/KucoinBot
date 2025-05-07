import pandas as pd
import logging
from indicators import compute_rsi, compute_macd, compute_atr

logger = logging.getLogger(__name__)

FIBO_LOWER = 0.382
FIBO_UPPER = 0.886
RSI_LONG = 45
RSI_SHORT = 55
WINDOW = 20

def detect_fvg(df: pd.DataFrame) -> bool:
    lows, highs = df['low'].values, df['high'].values
    for i in range(2, len(df)):
        if highs[i-2] < lows[i]:
            return True
    return False

def detect_fvg_short(df: pd.DataFrame) -> bool:
    lows, highs = df['low'].values, df['high'].values
    for i in range(2, len(df)):
        if lows[i-2] > highs[i]:
            return True
    return False

def analyze_market(symbol: str, df: pd.DataFrame, side: str = "long"):
    if len(df) < WINDOW:
        logger.info(f"{symbol} [4H] rejet length ({len(df)}<{WINDOW})")
        return None

    price = df['close'].iloc[-1]
    swing_high = df['high'].rolling(WINDOW).max().iloc[-2]
    swing_low  = df['low'].rolling(WINDOW).min().iloc[-2]

    if side == "long":
        fib_min = swing_low + FIBO_LOWER * (swing_high - swing_low)
        fib_max = swing_low + FIBO_UPPER * (swing_high - swing_low)
    else:
        fib_max = swing_high - FIBO_LOWER * (swing_high - swing_low)
        fib_min = swing_high - FIBO_UPPER * (swing_high - swing_low)

    if not (fib_min <= price <= fib_max):
        logger.info(f"{symbol} [4H] rejet OTE : price={price:.4f} hors zone [{fib_min:.4f}-{fib_max:.4f}]")
        return None

    ma50 = df['close'].rolling(50).mean().iloc[-1]
    ma200 = df['close'].rolling(200).mean().iloc[-1]

    if side == "long" and not (ma50 > ma200 and price > ma200):
        logger.info(f"{symbol} [4H] rejet trend LONG : ma50={ma50:.4f}, ma200={ma200:.4f}")
        return None
    if side == "short" and not (ma50 < ma200 and price < ma200):
        logger.info(f"{symbol} [4H] rejet trend SHORT : ma50={ma50:.4f}, ma200={ma200:.4f}")
        return None

    rsi = compute_rsi(df['close'], 14).iloc[-1]
    macd, signal, _ = compute_macd(df['close'])
    macd_val = macd.iloc[-1]
    sig_val = signal.iloc[-1]

    if side == "long" and not (rsi < RSI_LONG and macd_val > sig_val):
        logger.info(f"{symbol} [4H] rejet RSI/MACD LONG : RSI={rsi:.1f}, MACD={macd_val:.4f}, SIG={sig_val:.4f}")
        return None
    if side == "short" and not (rsi > RSI_SHORT and macd_val < sig_val):
        logger.info(f"{symbol} [4H] rejet RSI/MACD SHORT : RSI={rsi:.1f}, MACD={macd_val:.4f}, SIG={sig_val:.4f}")
        return None

    if side == "long" and not detect_fvg(df):
        logger.info(f"{symbol} [4H] rejet FVG LONG")
        return None
    if side == "short" and not detect_fvg_short(df):
        logger.info(f"{symbol} [4H] rejet FVG SHORT")
        return None

    atr = compute_atr(df, 14).iloc[-1]
    buffer = atr * 0.2

    if side == "long":
        entry = fib_min
        stop_loss = swing_low - buffer
        rr = entry - stop_loss
        tp1, tp2 = entry + rr, entry + 2 * rr
    else:
        entry = fib_max
        stop_loss = swing_high + buffer
        rr = stop_loss - entry
        tp1, tp2 = entry - rr, entry - 2 * rr

    return {
        "entry_min": float(fib_min),
        "entry_max": float(fib_max),
        "entry_price": float(entry),
        "stop_loss": float(stop_loss),
        "tp1": float(tp1),
        "tp2": float(tp2),
    }
