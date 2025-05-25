import requests
import time

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


# 🔁 Système de cache pour limiter les requêtes à l'API CoinGecko (une seule toutes les 5 minutes)
_cached_macro = None
_last_macro_check = 0

def is_macro_favorable(direction="long"):
    """
    Analyse la tendance du marché global (TOTAL et BTC.D) pour filtrer les signaux :
    - Pour un LONG : TOTAL doit monter et BTC.D ne doit pas monter
    - Pour un SHORT : TOTAL doit baisser et BTC.D ne doit pas baisser
    Cette version est optimisée pour éviter les erreurs API sur Railway.
    """
    global _cached_macro, _last_macro_check
    now = time.time()

    # ⚠️ Mise en cache pendant 5 minutes
    if _cached_macro is not None and now - _last_macro_check < 300:
        return _cached_macro

    try:
        r = requests.get("https://api.coingecko.com/api/v3/global", timeout=10)
        if r.status_code != 200:
            print(f"⚠️ API CoinGecko indisponible (code {r.status_code})")
            return False

        raw = r.json()
        if "data" not in raw:
            print(f"⚠️ Réponse CoinGecko invalide : {raw}")
            return False

        data = raw["data"]
        market_cap_change = data.get("market_cap_change_percentage_24h_usd", 0)
        btc_dominance = data.get("market_cap_percentage", {}).get("btc", 50)

        total_up = market_cap_change > 0
        btc_up = btc_dominance > 52  # seuil de dominance, ajustable

        if direction.lower() == "long":
            result = total_up and not btc_up
        else:
            result = not total_up and btc_up

        _cached_macro = result
        _last_macro_check = now
        return result

    except Exception as e:
        print(f"⚠️ Erreur macro check (TOTAL/BTC.D): {e}")
        return False
