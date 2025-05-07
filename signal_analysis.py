# signal_analysis.py

import pandas as pd
from indicators import compute_rsi, compute_macd, compute_atr

# ─── Paramètres partagés ───
FIBO_LOWER = 0.382  # 38,2 %
FIBO_UPPER = 0.886  # 88,6 %
RSI_LONG   = 45     # RSI <45 pour long
RSI_SHORT  = 55     # RSI >55 pour short
WINDOW     = 20

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
        return None

    # 1) Swing high/low
    swing_high = df['high'].rolling(WINDOW).max().iloc[-2]
    swing_low  = df['low'].rolling(WINDOW).min().iloc[-2]
    price      = df['close'].iloc[-1]

    # 2) FibO élargi
    if side == "long":
        fib_min = swing_low + FIBO_LOWER * (swing_high - swing_low)
        fib_max = swing_low + FIBO_UPPER * (swing_high - swing_low)
    else:
        fib_max = swing_high - FIBO_LOWER * (swing_high - swing_low)
        fib_min = swing_high - FIBO_UPPER * (swing_high - swing_low)
    if not (fib_min <= price <= fib_max):
        return None

    # 3) Trend (MA50 vs MA200)
    ma50  = df['close'].rolling(50).mean().iloc[-1]
    ma200 = df['close'].rolling(200).mean().iloc[-1]
    if side == "long" and not (ma50 > ma200 and price > ma200):
        return None
    if side == "short" and not (ma50 < ma200 and price < ma200):
        return None

    # 4) RSI & MACD
    rsi       = compute_rsi(df['close'], 14).iloc[-1]
    macd, sig, _ = compute_macd(df['close'])
    macd_val  = macd.iloc[-1]
    sig_val   = sig.iloc[-1]
    if side == "long" and not (rsi < RSI_LONG and macd_val > sig_val):
        return None
    if side == "short" and not (rsi > RSI_SHORT and macd_val < sig_val):
        return None

    # 5) Fair Value Gap
    if side == "long" and not detect_fvg(df):
        return None
    if side == "short" and not detect_fvg_short(df):
        return None

    # 6) SL / TP based on ATR
    atr        = compute_atr(df, 14).iloc[-1]
    buffer_atr = atr * 0.2
    if side == "long":
        entry      = fib_min
        stop_loss  = swing_low - buffer_atr
        rr         = entry - stop_loss
        tp1, tp2   = entry + rr, entry + 2*rr
    else:
        entry      = fib_max
        stop_loss  = swing_high + buffer_atr
        rr         = stop_loss - entry
        tp1, tp2   = entry - rr, entry - 2*rr

    return {
        "entry_min":   float(fib_min),
        "entry_max":   float(fib_max),
        "entry_price": float(entry),
        "stop_loss":   float(stop_loss),
        "tp1":         float(tp1),
        "tp2":         float(tp2),
    }
