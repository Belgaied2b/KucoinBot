import requests
import time
import pandas as pd

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
    return True  # laissé en placeholder

# ⚠️ Nouveau filtre macro intelligent
_cached_macro = None
_last_macro_check = 0

def fetch_macro_data():
    global _cached_macro, _last_macro_check
    now = time.time()
    if _cached_macro is not None and now - _last_macro_check < 300:
        return _cached_macro

    try:
        r = requests.get("https://api.coingecko.com/api/v3/global", timeout=10)
        data = r.json()["data"]
        total_change = data.get("market_cap_change_percentage_24h_usd", 0)
        btc_d = data.get("market_cap_percentage", {}).get("btc", 50)
        _cached_macro = {"total_change_24h": total_change, "btc_dominance": btc_d}
        _last_macro_check = now
        return _cached_macro
    except Exception as e:
        print("⚠️ Erreur API CoinGecko :", e)
        return {"total_change_24h": 0, "btc_dominance": 50}

def is_macro_context_favorable(symbol, direction, btc_df, total_df):
    macro = fetch_macro_data()
    total_change = macro["total_change_24h"]
    btc_d = macro["btc_dominance"]

    total_ma = total_df['close'].rolling(window=50).mean()
    total_trend = total_df['close'].iloc[-1] > total_ma.iloc[-1]

    btc_ma200 = btc_df['close'].rolling(window=200).mean()
    btc_price = btc_df['close'].iloc[-1]
    btc_above_ma = btc_price > btc_ma200.iloc[-1]
    btc_range = abs(btc_df['high'].iloc[-1] - btc_df['low'].iloc[-1]) < btc_df['close'].iloc[-1] * 0.01

    macro_score = 0
    notes = []

    if direction == "long":
        if total_trend or total_change > 0.5:
            macro_score += 1
        else:
            notes.append("❌ TOTAL faible")
        if btc_d < 52:
            macro_score += 1
        else:
            notes.append("❌ BTC.D en hausse")
        if btc_above_ma and not btc_range:
            macro_score += 1
        else:
            notes.append("❌ BTC faible ou en range")

    elif direction == "short":
        if not total_trend or total_change < -0.5:
            macro_score += 1
        else:
            notes.append("❌ TOTAL encore haussier")
        if btc_d > 50:
            macro_score += 1
        else:
            notes.append("❌ BTC.D en baisse")
        if not btc_above_ma:
            macro_score += 1
        else:
            notes.append("❌ BTC reste haussier")

    if macro_score == 3:
        return True, "✅ Macro favorable", 0
    elif macro_score == 2:
        return True, "⚠️ Macro partiellement favorable : " + ", ".join(notes), -1
    else:
        return False, "❌ Macro défavorable : " + ", ".join(notes), -2
