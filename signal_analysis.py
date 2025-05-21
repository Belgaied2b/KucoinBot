from kucoin_utils import fetch_klines
from indicators import (
    compute_atr,
    compute_ote,
    compute_fvg,
    compute_ma,
    find_pivots
)
from scanner import is_cos_valid, is_bos_valid, is_btc_favorable

def analyze_signal(df, direction="long"):
    symbol = getattr(df, 'name', 'UNKNOWN')
    dir_up = direction.lower() == "long"
    dir_str = direction.upper()

    print(f"[{symbol}] ➡️ Analyse {dir_str}")

    atr    = compute_atr(df).iloc[-1]
    ote    = compute_ote(df).iloc[-1]
    fvg    = compute_fvg(df).iloc[-1]
    ma200  = compute_ma(df, 200).iloc[-1]
    highs, lows = find_pivots(df, window=5)
    entry  = df['close'].iloc[-1]

    # Zones OTE & FVG
    ote_upper, ote_lower = ote['ote_upper'], ote['ote_lower']
    fvg_upper, fvg_lower = fvg['fvg_upper'], fvg['fvg_lower']
    in_ote = (ote_lower <= entry <= ote_upper)
    in_fvg = (fvg_lower <= entry <= fvg_upper)

    # Validations structurelles
    bos_ok = is_bos_valid(df, direction)
    cos_ok = is_cos_valid(df, direction)
    btc_ok = is_btc_favorable()
    ma_ok  = (entry > ma200) if dir_up else (entry < ma200)

    # Logs
    print(f"[{symbol}]   Entry        : {entry:.4f}")
    print(f"[{symbol}]   OTE valid    : {in_ote}")
    print(f"[{symbol}]   FVG valid    : {in_fvg}")
    print(f"[{symbol}]   BOS valid    : {bos_ok}")
    print(f"[{symbol}]   COS valid    : {cos_ok}")
    print(f"[{symbol}]   BTC trend    : {btc_ok}")
    print(f"[{symbol}]   MA200 trend  : {ma_ok}")

    # Rejet si un filtre échoue
    checks = {
        "OTE": in_ote,
        "FVG": in_fvg,
        "BOS": bos_ok,
        "COS": cos_ok,
        "BTC": btc_ok,
        "MA200": ma_ok
    }
    failed = [k for k, v in checks.items() if not v]
    if failed:
        print(f"[{symbol}] ❌ Rejeté ({', '.join(failed)})\n")
        return None

    # SL basé sur pivot structurel
    if dir_up and lows:
        sl = df['low'].iloc[lows[-1]]
    elif not dir_up and highs:
        sl = df['high'].iloc[highs[-1]]
    else:
        sl = df['low'].iloc[-1] if dir_up else df['high'].iloc[-1]

    # SL sécurisé (min/max)
    min_dist = atr * 1.5
    max_dist = entry * 0.06
    current_dist = abs(entry - sl)
    if current_dist < min_dist:
        sl = entry - min_dist if dir_up else entry + min_dist
    elif current_dist > max_dist:
        sl = entry - max_dist if dir_up else entry + max_dist

    # TP1 = dernier swing high/low
    if dir_up and highs:
        tp1 = df['high'].iloc[highs[-1]]
    elif not dir_up and lows:
        tp1 = df['low'].iloc[lows[-1]]
    else:
        tp1 = entry + atr * 3 if dir_up else entry - atr * 3

    # TP2 = extension (même distance que Entry→TP1)
    extension = abs(tp1 - entry)
    tp2 = tp1 + extension if dir_up else tp1 - extension

    # R:R
    risk = abs(entry - sl)
    rr1 = round(abs(tp1 - entry) / risk, 2)
    rr2 = round(abs(tp2 - entry) / risk, 2)

    print(f"[{symbol}] ✅ Confirmé (RR1={rr1}, RR2={rr2}) | SL={sl:.4f} | TP1={tp1:.4f} | TP2={tp2:.4f}\n")

    return {
        "type":      "CONFIRMÉ",
        "direction": dir_str,
        "entry":     entry,
        "sl":        sl,
        "tp":        tp1,
        "tp1":       tp1,
        "tp2":       tp2,
        "rr":        rr1,
        "rr1":       rr1,
        "rr2":       rr2,
        "ote_zone":  (ote_upper, ote_lower),
        "fvg_zone":  (fvg_upper, fvg_lower),
        "ma200":     ma200,
        "symbol":    symbol,
        "comment":   f"🎯 Confirmé (TP1 structure, TP2 extension, R:R1={rr1}, R:R2={rr2})"
    }
