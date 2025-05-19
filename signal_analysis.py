# signal_analysis.py

def analyze_signal(df, direction="long"):
    from indicators import compute_rsi, compute_macd, compute_fvg, compute_ote
    from risk_manager import calculate_rr
    from scanner import is_cos_valid, is_bos_valid, is_btc_favorable

    try:
        rsi_series = compute_rsi(df['close'])
        macd_line, signal_line = compute_macd(df['close'])

        fvg_info = compute_fvg(df, direction)
        ote_info = compute_ote(df, direction)
        entry = ote_info["entry"]
        sl = fvg_info["sl"]
        price = df["close"].iloc[-1]

        ma200 = df["close"].rolling(200).mean().iloc[-1]
        ma_ok = price > ma200 if direction == "long" else price < ma200
        cos = is_cos_valid(df)
        bos = is_bos_valid(df)
        btc_ok = is_btc_favorable()

        # ❌ Rejet si l'un des critères majeurs échoue
        if not all([fvg_info["valid"], ote_info["in_ote"], cos, bos, ma_ok, btc_ok]):
            return None

        # ✅ SL doit être défini
        if sl is None:
            return None

        # ✅ Calcul TP
        tp = calculate_rr(entry, sl, rr_ratio=2.5, direction=direction)
        rr = abs((tp - entry) / (entry - sl))
        if rr < 1.5:
            return None

        comment = f"🎯 Signal confirmé – entrée idéale après repli\n✔️ R:R = {rr:.2f}"

        return {
            "type": "CONFIRMÉ",
            "direction": direction.upper(),
            "entry": round(entry, 8),
            "sl": round(sl, 8),
            "tp": round(tp, 8),
            "rsi": round(rsi_series.iloc[-1], 2),
            "macd": round(macd_line.iloc[-1], 6),
            "signal_line": round(signal_line.iloc[-1], 6),
            "comment": comment,
            "ote_zone": ote_info["zone"],
            "fvg_zone": fvg_info["zone"] if "zone" in fvg_info else None
        }

    except Exception as e:
        print(f"[{df.name}] ⚠️ Erreur dans analyze_signal : {e}")
        return None
