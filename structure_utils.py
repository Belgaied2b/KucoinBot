import pandas as pd

def detect_bos_cos(df, direction="long", lookback=20):
    """
    Détecte un Break of Structure (BOS) et un Change of Structure (COS)
    BOS : cassure dans le sens de la tendance.
    COS : cassure dans le sens opposé à la tendance.
    """
    if df is None or len(df) < lookback + 3:
        return False, False

    df = df.copy()
    recent = df.iloc[-(lookback + 3):-3]  # On prend les bougies antérieures, on exclut les 3 dernières
    prev_high = recent['high'].max()
    prev_low = recent['low'].min()
    current_close = df['close'].iloc[-1]

    bos = False
    cos = False

    if direction == "long":
        bos = current_close > prev_high  # cassure vers le haut
        cos = current_close < prev_low   # cassure vers le bas (structure brisée)
    else:
        bos = current_close < prev_low   # cassure vers le bas
        cos = current_close > prev_high  # cassure vers le haut (structure brisée)

    return bos, cos


def detect_choch(df, direction="long", lookback=20):
    """
    Détecte un CHoCH (Change of Character) = cassure du pivot opposé à la tendance.
    Utile pour repérer un retournement de marché.
    """
    if df is None or len(df) < lookback + 3:
        return False

    df = df.copy()
    recent = df.iloc[-(lookback + 3):-3]  # Exclure les dernières bougies instables
    prev_high = recent['high'].max()
    prev_low = recent['low'].min()
    current_close = df['close'].iloc[-1]

    if direction == "long":
        return current_close < prev_low  # cassure d'un bas = retournement baissier
    else:
        return current_close > prev_high  # cassure d'un haut = retournement haussier
