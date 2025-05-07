import pandas as pd
from indicators import compute_rsi, compute_macd, compute_atr

def analyze_signal(df_1h, df_4h, direction="long"):
    if df_1h.empty or df_4h.empty:
        print(f"[❌] Données manquantes")
        return None, None, None, None

    rsi = compute_rsi(df_1h['close'])
    macd_line, signal_line = compute_macd(df_1h['close'])
    atr = compute_atr(df_4h)

    price = df_4h['close'].iloc[-1]
    high = df_4h['high'].rolling(20).max().iloc[-2]
    low = df_4h['low'].rolling(20).min().iloc[-2]
    last_rsi = rsi.iloc[-1]
    last_macd = macd_line.iloc[-1]
    last_signal = signal_line.iloc[-1]
    last_atr = atr.iloc[-1]

    if direction == "long":
        fib618 = low + 0.618 * (high - low)
        fib786 = low + 0.786 * (high - low)
        in_ote = fib618 <= price <= fib786
        fvg_valid = price <= high + 5
        entry = fib618
        sl = low - last_atr
        tp = entry + 1.618 * (entry - low)
        context_ok = 40 < last_rsi < 70 and last_macd > last_signal * 1.05

    else:  # SHORT
        fib618 = high - 0.618 * (high - low)
        fib786 = high - 0.786 * (high - low)
        in_ote = fib786 <= price <= fib618
        fvg_valid = price >= low - 5
        entry = fib618
        sl = high + last_atr
        tp = entry - 1.618 * (high - entry)
        context_ok = last_rsi > 70 and last_macd < last_signal * 0.95

    print(f"[🧪] {direction.upper()} | Price={price:.4f} | RSI={last_rsi:.2f} | MACD={last_macd:.4f} | Signal={last_signal:.4f}")
    print(f"↪️ OTE={in_ote} | FVG={fvg_valid} | Contexte OK={context_ok}")

    if context_ok and in_ote and fvg_valid:
        print(f"✅ Signal CONFIRMÉ ({direction})")
        return "confirmé", entry, sl, tp
    elif context_ok:
        print(f"🧠 Signal ANTICIPÉ ({direction})")
        return "anticipé", entry, sl, tp

    print(f"❌ Aucun signal ({direction})")
    return None, None, None, None
