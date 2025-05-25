import requests

def is_cos_valid(df, direction):
    window = 5
    if direction == "long":
        last_pivot_low = df['low'].rolling(window).min().iloc[-1]
        return df['close'].iloc[-1] > last_pivot_low * 1.02
    else:
        last_pivot_high = df['high'].rolling(window).max().iloc[-1]
        return df['close'].iloc[-1] < last_pivot_high * 0.98

def is_bos_valid(df, direction):
    highs = df['high'].rolling(5).max()
    lows = df['low'].rolling(5).min()
    if direction == "long":
        return df['close'].iloc[-1] > highs.iloc[-5]
    else:
        return df['close'].iloc[-1] < lows.iloc[-5]

def is_btc_favorable():
    return True  # simulé ou à adapter

def is_macro_favorable(direction="long"):
    """
    Analyse la tendance macro : CoinGecko TOTAL + BTC.D
    - LONG : TOTAL ↑ et BTC.D ↘
    - SHORT : TOTAL ↓ et BTC.D ↑
    """
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
        btc_up = btc_dominance > 52  # seuil ajustable

        if direction.lower() == "long":
            return total_up and not btc_up
        else:
            return not total_up and btc_up

    except Exception as e:
        print(f"⚠️ Erreur macro check (TOTAL/BTC.D): {e}")
        return False
