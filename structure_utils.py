def is_cos_valid(df, direction):
    """
    Détection simplifiée du COS (Change of Structure)
    Retourne True si un swing inverse s'est formé récemment.
    """
    window = 5
    if direction == "long":
        last_pivot_low = df['low'].rolling(window).min().iloc[-1]
        return df['close'].iloc[-1] > last_pivot_low * 1.02
    else:
        last_pivot_high = df['high'].rolling(window).max().iloc[-1]
        return df['close'].iloc[-1] < last_pivot_high * 0.98

def is_bos_valid(df, direction):
    """
    Détection simplifiée du BOS (Break of Structure)
    Retourne True si le dernier prix casse le plus haut ou plus bas récent.
    """
    highs = df['high'].rolling(5).max()
    lows = df['low'].rolling(5).min()
    if direction == "long":
        return df['close'].iloc[-1] > highs.iloc[-5]
    else:
        return df['close'].iloc[-1] < lows.iloc[-5]

def is_btc_favorable():
    # Simulation simple de tendance BTC (à adapter selon ton système réel)
    return True
