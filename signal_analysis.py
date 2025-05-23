from kucoin_utils import fetch_klines
from indicators import (
    compute_atr,
    compute_ote,
    compute_fvg,
    compute_ma,
    find_pivots
)
from structure_utils import is_cos_valid, is_bos_valid, is_btc_favorable

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

    # Zones
    ote_upper, ote_lower = ote['ote_upper'], ote['ote_lower']
    fvg_upper, fvg_lower = fvg['fvg_upper'], fvg['fvg_lower']
    in_ote = (ote_lower <= entry <= ote_upper)
    in_fvg = (fvg_lower <= entry <= fvg_upper)

    # Structure & tendance
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

    
    # Validation de la bougie (ex : clôture dans OTE + FVG, bougie haussière pour long)
    last_open = df['open'].iloc[-1]
    last_close = df['close'].iloc[-1]
    last_volume = df['volume'].iloc[-1]
    avg_volume = df['volume'].rolling(window=20).mean().iloc[-1]
    bougie_valide = (last_close > last_open) if dir_up else (last_close < last_open)
    cloture_dans_zones = in_ote and in_fvg

    # Score qualité
    score = 0
    if in_ote: score += 1
    if in_fvg: score += 1
    if bos_ok: score += 2
    if cos_ok: score += 2
    if btc_ok: score += 1
    if ma_ok: score += 1
    if bougie_valide: score += 1
    if last_volume >= avg_volume: score += 1

    print(f"[{symbol}]   Bougie valide : {bougie_valide}")
    print(f"[{symbol}]   Volume OK     : {last_volume >= avg_volume} (actuel: {last_volume:.2f} / moy: {avg_volume:.2f})")
    print(f"[{symbol}]   Score qualité : {score}/10")

    if score < 7:
        print(f"[{symbol}] ❌ Rejeté (score qualité insuffisant)\n")
        return None

    
    # SL structurel amélioré (pivot - ATR) sans contrainte max_dist
    if dir_up and lows:
        pivot = df['low'].iloc[lows[-1]]
        sl = pivot - atr
    elif not dir_up and highs:
        pivot = df['high'].iloc[highs[-1]]
        sl = pivot + atr
    else:
        sl = df['low'].iloc[-1] - atr if dir_up else df['high'].iloc[-1] + atr

    # TP1 intelligent : pivot avec RR ≥ 1.5
    pivots = highs if dir_up else lows
    tp1 = None
    for i in reversed(pivots):
        level = df['high'].iloc[i] if dir_up else df['low'].iloc[i]
        rr = (level - entry) / (entry - sl) if dir_up else (entry - level) / (sl - entry)
        if rr >= 1.5:
            tp1 = level - atr * 0.2 if dir_up else level + atr * 0.2
            break

    if tp1 is None:
        print(f"[{symbol}] ❌ Aucun TP1 structurel avec RR ≥ 1.5 trouvé\n")
        return None

    # TP2 = extension
    extension = abs(tp1 - entry)
    tp2 = tp1 + extension if dir_up else tp1 - extension

    # R:R
    risk = abs(entry - sl)
    rr1 = round(abs(tp1 - entry) / risk, 2)
    rr2 = round(abs(tp2 - entry) / risk, 2)

