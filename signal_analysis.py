# signal_analysis.py

def analyze_signal(df, direction="long"):
    """
    Analyse technique pour valider un signal CONFIRMÉ.
    - R:R non bloquant
    - Ajoute log terminal et commentaire Telegram si R:R < 1.5
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

        if not fvg_valid or not in_ote:
            print(f"[{df.name}] ❌ Rejeté : FVG={fvg_valid} | OTE={in_ote} | MA OK={ma_ok} | R:R=N/A")
            return None

        entry = ote_zone["entry"]
        sl = fvg_info["sl"]
        tp = calculate_rr(entry, sl, rr_ratio=2.5, direction=direction)
        rr = abs((tp - entry) / (entry - sl))

        if rr < 1.5:
            print(f"[{df.name}] ⚠️ R:R faible : {rr:.2f} (signal quand même envoyé)")
            rr_comment = f"⚠️ R:R = {rr:.2f} (risque élevé)"
        else:
            print(f"[{df.name}] ✅ Signal validé : entry={entry:.8f} | SL={sl:.8f} | TP={tp:.8f} | R:R={rr:.2f}")
            rr_comment = f"✔️ R:R = {rr:.2f}"

        comment = f"🎯 Signal confirmé – entrée idéale après repli\n{rr_comment}"

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
