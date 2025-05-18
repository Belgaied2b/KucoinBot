# signal_analysis.py

def analyze_signal(df, direction="long"):
    """
    Analyse technique pour valider un signal CONFIRMÉ.
    Repose uniquement sur les vérifications externes (COS, BOS).
    Ajoute des logs de rejet si les conditions échouent.
    """

    try:
        from indicators import compute_rsi, compute_macd, compute_fvg, compute_ote
        from risk_manager import calculate_rr

        rsi_series = compute_rsi(df['close'])
        macd_line, signal_line = compute_macd(df['close'])
        fvg_info = compute_fvg(df)
        ote_zone = compute_ote(df, direction)

        price = df['close'].iloc[-1]
        current_rsi = rsi_series.iloc[-1]
        current_macd = macd_line.iloc[-1]
        current_signal = signal_line.iloc[-1]

        fvg_valid = fvg_info["valid"]
        in_ote = ote_zone["in_ote"]
        ma200 = df['close'].rolling(200).mean().iloc[-1]
        ma_ok = price > ma200 if direction == "long" else price < ma200

        # Log préliminaire si bloqué par structure
        if not fvg_valid or not in_ote or not ma_ok:
            print(f"[{df.name}] ❌ Rejeté : FVG={fvg_valid} | OTE={in_ote} | MA OK={ma_ok} | R:R=N/A")
            return None

        entry = ote_zone["entry"]
        sl = fvg_info["sl"]
        tp = calculate_rr(entry, sl, rr_ratio=2.5, direction=direction)

        rr = abs((tp - entry) / (entry - sl))
        if rr < 1.5:
            print(f"[{df.name}] ❌ Rejeté : FVG=True | OTE=True | MA OK=True | R:R={rr:.2f} ❌")
            return None

        comment = "🎯 Signal confirmé – entrée idéale après repli"

        print(f"[{df.name}] ✅ Signal validé : entry={entry:.8f} | SL={sl:.8f} | TP={tp:.8f} | R:R={rr:.2f}")

        return {
            "type": "CONFIRMÉ",
            "direction": direction.upper(),
            "entry": round(entry, 8),
            "sl": round(sl, 8),
            "tp": round(tp, 8),
            "rsi": round(current_rsi, 2),
            "macd": round(current_macd, 6),
            "signal_line": round(current_signal, 6),
            "comment": comment
        }

    except Exception as e:
        print(f"[{df.name}] ⚠️ Erreur dans analyze_signal : {e}")
        return None
