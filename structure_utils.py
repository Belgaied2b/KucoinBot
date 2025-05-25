import requests

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
    # Simulation simple (à remplacer par détection réelle sur BTC si besoin)
    return True

def is_macro_favorable(direction="long"):
    """
    Analyse la tendance du marché global (TOTAL et BTC.D) pour filtrer les signaux :
    - Pour un LONG : TOTAL doit monter et BTC.D ne doit pas monter
    - Pour un SHORT : TOTAL doit baisser et BTC.D ne doit pas baisser
    """
    try:
        r_total = requests.get("https://api.coingecko.com/api/v3/global")
        data = r_total.json()

        market_cap_change = data["data"]["market_cap_change_percentage_24h_usd"]
        btc_dominance_change = data["data"]["market_cap_percentage"]["btc"]

        # Tendance globale : on compare avec des seuils simples
        total_up = market_cap_change > 0
        btc_up = btc_dominance_change > 52  # seuil ajustable

        if direction.lower() == "long":
            return total_up and not btc_up
        else:
            return not total_up and btc_up
    except Exception as e:
        print(f"⚠️ Erreur macro check (TOTAL/BTC.D): {e}")
        return False
