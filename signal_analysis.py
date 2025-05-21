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
    # Stub simple : toujours valide
    return True

def is_bos_valid(df):
    # Stub simple : toujours valide
    return True

def is_btc_favorable():
    # Stub simple : toujours favorable
    return True

def analyze_signal(df, direction="long"):
    # 1) Calcul des indicateurs
    atr    = compute_atr(df).iloc[-1]
    ote    = compute_ote(df).iloc[-1]
    fvg    = compute_fvg(df).iloc[-1]
    ma200  = compute_ma(df, period=200).iloc[-1]
    highs, lows = find_pivots(df, window=5)
    entry  = df['close'].iloc[-1]

    # 2) Niveaux initiaux
    if direction.lower() == "long":
        sl = df['low'].iloc[-1]
        tp = entry + (entry - sl) * 2.5
    else:
        sl = df['high'].iloc[-1]
        tp = entry - (sl - entry) * 2.5

    # 3) Ajustement ATR et % de l’entry
    min_sl = atr * 1.5
    max_sl = entry * 0.06
    dist   = abs(entry - sl)
    if dist < min_sl:
        sl = entry + min_sl if direction.lower()=="short" else entry - min_sl
    if dist > max_sl:
        sl = entry + max_sl if direction.lower()=="short" else entry - max_sl

    # 4) Ancrage sur pivots (buffer 20% ATR)
    if direction.lower()=="long" and lows:
        pivot = df['low'].iloc[lows[-1]]
        sl = min(sl, pivot - atr * 0.2)
    if direction.lower()=="short" and highs:
        pivot = df['high'].iloc[highs[-1]]
        sl = max(sl, pivot + atr * 0.2)

    # 5) Calcul du R:R exact
    if direction.lower() == "long":
        rr = (tp - entry) / (entry - sl)
    else:
        rr = (entry - tp) / (sl - entry)
    rr = round(rr, 2)

    return {
        "type":          "CONFIRMÉ",
        "direction":     direction.upper(),
        "entry":         entry,
        "sl":            sl,
        "tp":            tp,
        "rr":            rr,
        "ote_zone":      (ote['ote_upper'], ote['ote_lower']),
        "fvg_zone":      (fvg['fvg_upper'], fvg['fvg_lower']),
        "ma200":         ma200,
        "cos_valid":     is_cos_valid(df),
        "bos_valid":     is_bos_valid(df),
        "btc_favorable": is_btc_favorable(),
        "symbol":        getattr(df, 'name', 'UNKNOWN'),
        "comment":       "🎯 Signal confirmé – entrée idéale après repli"
    }
