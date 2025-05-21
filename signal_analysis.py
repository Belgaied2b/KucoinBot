from indicators import (
    compute_atr,
    compute_fvg,
    compute_ote,
    compute_rsi,
    compute_macd,
    find_pivots
)

# === Stubs de validation (auparavant dans scanner.py) ===
def is_cos_valid(df):
    """Vérifie la validité du Change Of Structure (COS)."""
    # TODO: implémenter la vraie logique
    return True

def is_bos_valid(df):
    """Vérifie la validité du Break Of Structure (BOS)."""
    # TODO: implémenter la vraie logique
    return True

def is_btc_favorable():
    """Vérifie si la tendance globale du BTC est favorable."""
    # TODO: implémenter la vraie logique
    return True

def analyze_signal(df, direction="long"):
    """
    Analyse et génère un signal.
    Retourne dict avec : type, direction, entry, sl, tp, rr, comment.
    """
    # Calcul des indicateurs
    atr = compute_atr(df).iloc[-1]
    highs, lows = find_pivots(df, window=5)

    # Niveau d'entrée (dernier close)
    entry = df['close'].iloc[-1]

    # SL/TP initiaux basés sur cos/bos ou plus simplement sur high/low
    if direction == "long":
        sl = df['low'].iloc[-1]
        tp = entry + (entry - sl) * 2.5
    else:  # short
        sl = df['high'].iloc[-1]
        tp = entry - (sl - entry) * 2.5

    # Ajustement min/max par ATR et % entry
    min_sl = atr * 1.5
    max_sl = entry * 0.06
    dist = abs(entry - sl)

    if dist < min_sl:
        sl = entry + min_sl if direction=="short" else entry - min_sl
    elif dist > max_sl:
        sl = entry + max_sl if direction=="short" else entry - max_sl

    # Ajustement pivot (zones S/R) avec buffer 20% ATR
    if direction == "long" and lows:
        pivot = df['low'].iloc[lows[-1]]
        sl = min(sl, pivot - atr * 0.2)
    if direction == "short" and highs:
        pivot = df['high'].iloc[highs[-1]]
        sl = max(sl, pivot + atr * 0.2)

    # Calcul exact du R:R
    if direction == "long":
        rr = (tp - entry) / (entry - sl)
    else:
        rr = (entry - tp) / (sl - entry)
    rr = round(rr, 2)

    return {
        "type": "CONFIRMÉ",
        "direction": direction,
        "entry": entry,
        "sl": sl,
        "tp": tp,
        "rr": rr,
        "comment": "🎯 Signal confirmé – entrée idéale après repli"
    }
