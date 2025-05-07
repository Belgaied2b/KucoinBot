# signal_analysis.py

import pandas as pd
from indicators import compute_rsi, compute_macd, compute_atr

# ─── Nouveaux paramètres partagés avec scanner.py ───
FIBO_LOWER = 0.382  # 38,2%
FIBO_UPPER = 0.886  # 88,6%
RSI_LONG   = 45     # RSI <45 pour long
RSI_SHORT  = 55     # RSI >55 pour short

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
    window = 20
    if len(df) < window:
        return None

    # 1) Swing
    swing_high = df['high'].rolling(window).max().iloc[-2]
    swing_low  = df['low'].rolling(window).min().iloc[-2]

    # 2) FibO élargi
    if side == "long":
        fib_min = swing_low + FIBO_LOWER * (swing_high - swing_low)
        fib_max = swing_low + FIBO_UPPER * (swing_high - swing_low)
    else:
        fib_max = swing_high - FIBO_LOWER * (swing_high - swing_low)
        fib_min = swing_high - FIBO_UPPER * (swing_high - swing_low)

    last_price = df['close'].iloc[-1]
    if not (fib_min <= last_price <= fib_max):
        return None

    # 3) Trend
    ma50, ma200 = (
        df['close'].rolling(50).mean().iloc[-1],
        df['close'].rolling(200).mean().iloc[-1]
    )
    if side=="long" and not (ma50>ma200 and last_price>ma200): return None
    if side=="short" and not (ma50<ma200 and last_price<ma200): return None

    # 4) RSI & MACD
    rsi = compute_rsi(df['close'], 14).iloc[-1]
    macd, signal_line, _ = compute_macd(df['close'])
    macd_val, sig_val = macd.iloc[-1], signal_line.iloc[-1]
    if side=="long":
        if not (rsi < RSI_LONG and macd_val > sig_val):
            return None
    else:
        if not (rsi > RSI_SHORT and macd_val < sig_val):
            return None

    # 5) FVG
    if side=="long" and not detect_fvg(df): return None
    if side=="short" and not detect_fvg_short(df): return None

    # 6) SL, TP1, TP2
    atr = compute_atr(df, 14).iloc[-1]
    buffer_atr = atr * 0.2
    if side=="long":
        entry = fib_min
        sl    = swing_low - buffer_atr
        rr    = entry - sl
        tp1   = entry + rr
        tp2   = entry + 2*rr
    else:
        entry = fib_max
        sl    = swing_high + buffer_atr
        rr    = sl - entry
        tp1   = entry - rr
        tp2   = entry - 2*rr

    return {
        "entry_min":   float(fib_min),
        "entry_max":   float(fib_max),
        "entry_price": float(entry),
        "stop_loss":   float(sl),
        "tp1":         float(tp1),
        "tp2":         float(tp2),
    }
