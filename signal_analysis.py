def analyze_signal(df, direction="long"):
    """
    Analyse complète d'un signal CONFIRMÉ :
    - Valide FVG, OTE, BOS, COS, MA200, BTC
    - SL défini à partir de la FVG
    - Si SL incohérent, recalculé automatiquement à -0.5% sous l'entrée
    """

    try:
        from indicators import compute_rsi, compute_macd, compute_fvg, compute_ote
        from risk_manager import calculate_rr
        from scanner import is_cos_valid, is_bos_valid, is_btc_favorable

        rsi_series = compute_rsi(df['close'])
        macd_line, signal_line = compute_macd(df['close'])

        fvg = compute_fvg(df, direction)
        ote = compute_ote(df, direction)

        price = df['close'].iloc[-1]
        entry = ote["entry"]
        sl = fvg["sl"]

        ma200 = df['close'].rolling(200).mean().iloc[-1]
        ma_ok = price > ma200 if direction == "long" else price < ma200
        cos = is_cos_valid(df)
        bos = is_bos_valid(df)
        btc_ok = is_btc_favorable()

        print(f"[{df.name}] Check: FVG={fvg['valid']} | OTE={ote['in_ote']} | COS={cos} | BOS={bos} | MA200={ma_ok} | BTC={btc_ok}")

        if not all([fvg["valid"], ote["in_ote"], cos, bos, ma_ok, btc_ok]):
            print(f"[{df.name}] ❌ Rejeté (structure invalide)")
            return None

        # ⚠️ Si SL incohérent → recalcul propre
        if (direction == "long" and sl >= entry) or (direction == "short" and sl <= entry):
            print(f"[{df.name}] ⚠️ SL incohérent. Recalcul automatique.")
            adjustment = 0.005  # 0.5%
            if direction == "long":
                sl = entry - (entry * adjustment)
            else:
                sl = entry + (entry * adjustment)

        # Calcul du TP
        tp = calculate_rr(entry, sl, rr_ratio=2.5, direction=direction)
        rr = abs((tp - entry) / (entry - sl))
        if rr < 1.5:
            print(f"[{df.name}] ❌ Rejeté : R:R={rr:.2f} < 1.5")
            return None

        comment = f"🎯 Signal confirmé – entrée idéale après repli\n✔️ R:R = {rr:.2f}"

        print(f"[{df.name}] ✅ Signal CONFIRMÉ : Entry={entry:.4f}, SL={sl:.4f}, TP={tp:.4f}, R:R={rr:.2f}")

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
            "ote_zone": ote["zone"],
            "fvg_zone": fvg["zone"] if "zone" in fvg else None
        }

    except Exception as e:
        print(f"[{df.name}] ⚠️ Erreur dans analyze_signal : {e}")
        return None
