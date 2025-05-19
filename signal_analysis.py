def analyze_signal(df, direction="long"):
    """
    Analyse complète d'un signal CONFIRMÉ pour swing :
    - FVG, OTE, BOS, COS, MA200, BTC validés
    - SL dynamique basé sur FVG + ATR + max 6%
    """

    try:
        from indicators import compute_rsi, compute_macd, compute_fvg, compute_ote, compute_atr
        from risk_manager import calculate_rr
        from scanner import is_cos_valid, is_bos_valid, is_btc_favorable

        rsi_series = compute_rsi(df['close'])
        macd_line, signal_line = compute_macd(df['close'])

        fvg = compute_fvg(df, direction)
        ote = compute_ote(df, direction)

        price = df['close'].iloc[-1]
        entry = ote["entry"]
        sl = fvg["sl"]
        atr = compute_atr(df).iloc[-1]

        ma200 = df['close'].rolling(200).mean().iloc[-1]
        ma_ok = price > ma200 if direction == "long" else price < ma200
        cos = is_cos_valid(df)
        bos = is_bos_valid(df)
        btc_ok = is_btc_favorable()

        print(f"[{df.name}] Check: FVG={fvg['valid']} | OTE={ote['in_ote']} | COS={cos} | BOS={bos} | MA200={ma_ok} | BTC={btc_ok}")

        if not all([fvg["valid"], ote["in_ote"], cos, bos, ma_ok, btc_ok]):
            print(f"[{df.name}] ❌ Rejeté (structure invalide)")
            return None

        # Recal SL si incohérent structurellement
        if (direction == "long" and sl >= entry) or (direction == "short" and sl <= entry):
            print(f"[{df.name}] ⚠️ SL incohérent. Recalcul automatique.")
            sl = entry - entry * 0.005 if direction == "long" else entry + entry * 0.005

        # 🔐 SL minimum = 1.5 × ATR
        min_sl_distance = atr * 1.5
        if direction == "long" and (entry - sl) < min_sl_distance:
            sl = entry - min_sl_distance
        elif direction == "short" and (sl - entry) < min_sl_distance:
            sl = entry + min_sl_distance

        # ⛔ SL maximum = 6%
        max_sl_distance = entry * 0.06
        if direction == "long" and (entry - sl) > max_sl_distance:
            sl = entry - max_sl_distance
        elif direction == "short" and (sl - entry) > max_sl_distance:
            sl = entry + max_sl_distance

        # 🎯 Calcul TP
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
            "fvg_zone": fvg["zone"] if "zone" in fvg else None,
            "symbol": df.name
        }

    except Exception as e:
        print(f"[{df.name}] ⚠️ Erreur dans analyze_signal : {e}")
        return None
