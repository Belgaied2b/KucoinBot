import pandas as pd
from indicators import compute_rsi, compute_macd, compute_atr

def analyze_signal(df_1h, df_4h=None, direction="long", test_mode=False):
    if df_1h.empty:
        print(f"[❌] Données manquantes pour analyse.")
        return None

    rsi = compute_rsi(df_1h['close'])
    macd_line, signal_line = compute_macd(df_1h['close'])
    atr = compute_atr(df_1h)

    price = df_1h['close'].iloc[-1]
    high = df_1h['high'].rolling(20).max().iloc[-2]
    low = df_1h['low'].rolling(20).min().iloc[-2]
    ma200 = df_1h['close'].rolling(200).mean().iloc[-1]

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
        sl = round(low - last_atr, 6)
        tp = round(entry + 2.5 * last_atr, 6)

        # 🔄 Filtres allégés
        context_ok = (
            40 < last_rsi < 70 and
            last_macd > last_signal * 0.98 and
            price > ma200
        )
    else:
        fib618 = high - 0.618 * (high - low)
        fib786 = high - 0.786 * (high - low)
        in_ote = fib786 <= price <= fib618
        fvg_valid = price >= low - 5
        entry = fib618
        sl = round(high + last_atr, 6)
        tp = round(entry - 2.5 * last_atr, 6)

        context_ok = (
            last_rsi > 70 and
            last_macd < last_signal * 1.02 and
            price < ma200
        )

    print(f"[🧪] {direction.upper()} | Price={price:.4f} | RSI={last_rsi:.2f} | MACD={last_macd:.4f} | Signal={last_signal:.4f}")
    print(f"↪️ OTE={in_ote} | FVG={fvg_valid} | MA200 OK={'YES' if (price > ma200 if direction=='long' else price < ma200) else 'NO'} | Context OK={context_ok}")

    if context_ok and in_ote and fvg_valid:
        return {
            "symbol": df_1h.name if hasattr(df_1h, "name") else "UNKNOWN",
            "type": "CONFIRMÉ",
            "direction": direction.upper(),
            "entry": round(entry, 6),
            "sl": sl,
            "tp": tp,
            "ote_zone": (round(fib786, 6), round(fib618, 6)),
            "fvg_zone": (round(high, 6), round(price, 6)),
            "comment": "Signal confirmé avec contexte élargi"
        }

    if test_mode and in_ote and fvg_valid:
        return {
            "symbol": df_1h.name if hasattr(df_1h, "name") else "UNKNOWN",
            "type": "TEST",
            "direction": direction.upper(),
            "entry": round(entry, 6),
            "sl": sl,
            "tp": tp,
            "ote_zone": (round(fib786, 6), round(fib618, 6)),
            "fvg_zone": (round(high, 6), round(price, 6)),
            "comment": "⚠️ Signal TEST (contexte souple)"
        }

    return None
