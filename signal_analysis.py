# /app/signal_analysis.py

from indicators import (
    compute_atr,
    compute_ote,
    compute_fvg,
    compute_rsi,
    compute_macd,
    compute_ma,
    find_pivots
)

def is_cos_valid(df):
    # === Votre logique COS ici ===
    # Ex. stub : condition toujours vraie (à remplacer)
    return True

def is_bos_valid(df):
    # === Votre logique BOS ici ===
    # Ex. stub : condition toujours vraie (à remplacer)
    return True

def is_btc_favorable():
    # === Votre logique tendance BTC ici ===
    # Ex. stub : condition toujours vraie (à remplacer)
    return True

def analyze_signal(df, direction="long"):
    symbol = getattr(df, 'name', 'UNKNOWN')
    dir_up = direction.lower() == "long"
    dir_str = direction.upper()

    # 📝 Démarrage de l’analyse
    print(f"[{symbol}] ➡️ Analyse {dir_str}")

    # 1) Calcul des indicateurs
    atr   = compute_atr(df).iloc[-1]
    ote   = compute_ote(df).iloc[-1]
    fvg   = compute_fvg(df).iloc[-1]
    ma200 = compute_ma(df, period=200).iloc[-1]
    highs, lows = find_pivots(df, window=5)
    entry = df['close'].iloc[-1]

    # 2) Validation des conditions
    cos_ok   = is_cos_valid(df)
    bos_ok   = is_bos_valid(df)
    btc_ok   = is_btc_favorable()
    ma200_ok = (entry > ma200) if dir_up else (entry < ma200)

    print(f"[{symbol}]   COS valid      : {cos_ok}")
    print(f"[{symbol}]   BOS valid      : {bos_ok}")
    print(f"[{symbol}]   BTC favorable  : {btc_ok}")
    print(f"[{symbol}]   MA200 trend    : {ma200_ok}")

    # Si l’un des tests échoue, on rejette
    checks = {
        "COS": cos_ok,
        "BOS": bos_ok,
        "BTC": btc_ok,
        "MA200": ma200_ok
    }
    failed = [name for name, ok in checks.items() if not ok]
    if failed:
        print(f"[{symbol}] ❌ Signal REJETÉ ({', '.join(failed)})\n")
        return None

    # 3) Niveaux initiaux Entry/SL/TP
    if dir_up:
        sl = df['low'].iloc[-1]
        tp = entry + (entry - sl) * 2.5
    else:
        sl = df['high'].iloc[-1]
        tp = entry - (sl - entry) * 2.5

    # 4) Ajustement ATR / % entry
    min_sl = atr * 1.5
    max_sl = entry * 0.06
    dist   = abs(entry - sl)
    if dist < min_sl:
        sl = entry + min_sl if not dir_up else entry - min_sl
    if dist > max_sl:
        sl = entry + max_sl if not dir_up else entry - max_sl

    # 5) Ancrage sur pivots (buffer 20% ATR)
    if dir_up and lows:
        pivot = df['low'].iloc[lows[-1]]
        sl = min(sl, pivot - atr * 0.2)
    if not dir_up and highs:
        pivot = df['high'].iloc[highs[-1]]
        sl = max(sl, pivot + atr * 0.2)

    # 6) Calcul exact du R:R
    rr = (tp - entry) / (entry - sl) if dir_up else (entry - tp) / (sl - entry)
    rr = round(rr, 2)

    # ✅ Signal confirmé
    print(f"[{symbol}] ✅ Signal CONFIRMÉ (RR={rr})\n")

    return {
        "type":          "CONFIRMÉ",
        "direction":     dir_str,
        "entry":         entry,
        "sl":            sl,
        "tp":            tp,
        "rr":            rr,
        "ote_zone":      (ote['ote_upper'], ote['ote_lower']),
        "fvg_zone":      (fvg['fvg_upper'], fvg['fvg_lower']),
        "ma200":         ma200,
        "symbol":        symbol,
        "comment":       "🎯 Signal confirmé – entrée idéale après repli"
    }
