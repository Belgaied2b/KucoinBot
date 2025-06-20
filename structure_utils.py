import pandas as pd

def detect_bos_cos(df, direction="long", lookback=20):
    """
    Détecte un Break of Structure (BOS) et un Change of Structure (COS)
    en comparant la clôture actuelle aux précédents hauts/bas significatifs.
    BOS = cassure dans le sens de la tendance.
    COS = cassure dans le sens opposé à la tendance.
    """
    if df is None or len(df) < lookback + 2:
        return False, False

    df = df.copy()
    recent = df[-(lookback+2):-2]  # On exclut les deux dernières bougies
    prev_high = recent['high'].max()
    prev_low = recent['low'].min()
    close = df['close'].iloc[-1]

    bos = False
    cos = False

    if direction == "long":
        if close > prev_high:
            bos = True
        if close < prev_low:
            cos = True
    else:
        if close < prev_low:
            bos = True
        if close > prev_high:
            cos = True

    return bos, cos


def detect_choch(df, direction="long", lookback=20):
    """
    Détecte un CHoCH (Change of Character) : cassure du dernier pivot opposé
    à la tendance actuelle, indiquant un possible retournement.
    Exemple : en tendance haussière, cassure du dernier bas structurel.
    """
    if df is None or len(df) < lookback + 2:
        return False

    df = df.copy()
    recent = df[-(lookback+2):-2]  # On exclut les deux dernières bougies
    prev_high = recent['high'].max()
    prev_low = recent['low'].min()
    close = df['close'].iloc[-1]

    if direction == "long":
        return close < prev_low
    else:
        return close > prev_high
