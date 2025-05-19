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
        entry = round(fib618, 6)
        sl = round(low - last_atr, 6)
        risk = round(entry - sl, 6)
        tp = round(entry + risk * 2.5, 6)
        ma_ok = price > ma200
    else:
        fib618 = high - 0.618 * (high - low)
        fib786 = high - 0.786 * (high - low)
        in_ote = fib786 <= price <= fib618
        fvg_valid = price >= low - 5
        entry = round(fib618, 6)
        sl = round(high + last_atr, 6)
        risk = round(sl - entry, 6)
        tp = round(entry - risk * 2.5, 6)
        ma_ok = price < ma200

    rr = round(abs(tp - entry) / abs(entry - sl), 2)

    print(f"[🧠] {direction.upper()} | Price={price:.4f} | RSI={last_rsi:.2f} | MACD={last_macd:.4f} | Signal={last_signal:.4f}")
    print(f"↪️ OTE={in_ote} | FVG={fvg_valid} | MA200 OK={'YES' if ma_ok else 'NO'} | R:R={rr}")

    # ✅ COS robuste
    lows = df_1h['low'].iloc[-9:]
    highs = df_1h['high'].iloc[-9:]
    cos = (
        lows.iloc[0] < lows.iloc[3] < lows.iloc[6] and
        highs.iloc[0] < highs.iloc[3] < highs.iloc[6]
    )

    # ✅ BOS
    recent_high = df_1h['high'].iloc[-5:-1].max()
    structure_ok = price > recent_high if direction == "long" else price < recent_high

    if not cos or not structure_ok:
        print(f"[🔁] Structure non valide : COS={cos} BOS={structure_ok}")
        return None

    signal_type = "CONFIRMÉ" if in_ote and fvg_valid else "ANTICIPÉ"
    comment = (
        "🎯 Signal confirmé – entrée idéale après repli"
        if signal_type == "CONFIRMÉ"
        else "⏳ Structure confirmée – attendre repli OTE/FVG"
    )

    return {
        "symbol": df_1h.name if hasattr(df_1h, "name") else "UNKNOWN",
        "type": signal_type,
        "direction": direction.upper(),
        "entry": entry,
        "sl": sl,
        "tp": tp,
        "rr": rr,
        "ote_zone": (round(fib786, 6), round(fib618, 6)),
        "fvg_zone": (round(high, 6), round(price, 6)),
        "comment": comment
    }
