# signal_analysis.py

def analyze_signal(df, direction="long"):
    """
    Analyse technique pour valider un signal CONFIRMÉ avec filtres stricts :
    - FVG valide
    - OTE valide
    - BOS + COS obligatoires
    - MA200 directionnelle
    - BTC favorable (reçu en paramètre ou à intégrer ailleurs)
    - R:R ≥ 1.5
    - RSI présent mais non bloquant
    """

    try:
        from indicators import compute_rsi, compute_macd, compute_fvg, compute_ote
        from risk_manager import calculate_rr
        from scanner import is_cos_valid, is_bos_valid, is_btc_favorable

        rsi_series = compute_rsi(df['close'])
        macd_line, signal_line = compute_macd(df['close'])
        fvg_info = compute_fvg(df, direction)
        ote_info = compute_ote(df, direction)

        current_rsi = rsi_series.iloc[-1]
        current_macd = macd_line.iloc[-1]
        current_signal = signal_line.iloc[-1]
        price = df['close'].iloc[-1]
        ma200 = df['close'].rolling(200).mean().iloc[-1]
        ma_ok = price > ma200 if direction == "long" else price < ma200
        cos = is_cos_valid(df)
        bos = is_bos_valid(df)
        btc_ok = is_btc_favorable()

        # Filtres bloquants
        if not all([fvg_info["valid"], ote_info["in_ote"], cos, bos, ma_ok, btc_ok]):
            print(f"[{df.name}] ❌ Rejeté : FVG={fvg_info['valid']} | OTE={ote_info['in_ote']} | COS={cos} | BOS={bos} | MA200={ma_ok} | BTC={btc_ok}")
            return None

        # SL uniquement via FVG
        sl = fvg_info["sl"]
        if sl is None:
            print(f"[{df.name}] ❌ Rejeté : SL non défini (FVG invalide)")
            return None

        entry = ote_info["entry"]
        tp = calculate_rr(entry, sl, rr_ratio=2.5, direction=direction)
        rr = abs((tp - entry) / (entry - sl))
        if rr < 1.5:
            print(f"[{df.name}] ❌ Rejeté : R:R = {rr:.2f} < 1.5")
            return None

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
