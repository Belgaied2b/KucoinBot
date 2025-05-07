# signal_analysis.py

import pandas as pd
from indicators import compute_rsi, compute_macd, compute_atr

# ─── Paramètres partagés ───
FIBO_LOWER = 0.382  # 38,2 %
FIBO_UPPER = 0.886  # 88,6 %
RSI_LONG   = 45
RSI_SHORT  = 55
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
    high_swing = df['high'].rolling(WINDOW).max().iloc[-2]
    low_swing  = df['low'].rolling(WINDOW).min().iloc[-2]
    last_price = df['close'].iloc[-1]

    # 2) FibO élargi
    if side == "long":
        fib_min = low_swing  + FIBO_LOWER * (high_swing - low_swing)
        fib_max = low_swing  + FIBO_UPPER * (high_swing - low_swing)
    else:
        fib_max = high_swing - FIBO_LOWER * (high_swing - low_swing)
        fib_min = high_swing - FIBO_UPPER * (high_swing - low_swing)
    if not (fib_min <= last_price <= fib_max):
        return None

    # 3) Trend (MA50/MA200)
    ma50  = df['close'].rolling(50).mean().iloc[-1]
    ma200 = df['close'].rolling(200).mean().iloc[-1]
    if side == "long":
        if not (ma50 > ma200 and last_price > ma200):
            return None
    else:
        if not (ma50 < ma200 and last_price < ma200):
            return None

    # 4) RSI & MACD (45/55 + crossover)
    rsi       = compute_rsi(df['close'], 14).iloc[-1]
    macd, sig, _ = compute_macd(df['close'])
    macd_val  = macd.iloc[-1]
    sig_val   = sig.iloc[-1]
    if side == "long":
        if not (rsi < RSI_LONG and macd_val > sig_val):
            return None
    else:
        if not (rsi > RSI_SHORT and macd_val < sig_val):
            return None

    # 5) Fair Value Gap
    if side == "long":
        if not detect_fvg(df):
            return None
    else:
        if not detect_fvg_short(df):
            return None

    # 6) Calcul SL/TP (ATR 14 + buffer 20 %)
    atr        = compute_atr(df, 14).iloc[-1]
    buffer_atr = atr * 0.2
    if side == "long":
        entry      = fib_min
        stop_loss  = low_swing - buffer_atr
        rr         = entry - stop_loss
        tp1, tp2   = entry + rr, entry + 2*rr
    else:
        entry      = fib_max
        stop_loss  = high_swing + buffer_atr
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
