import numpy as np
import pandas as pd
from structure_utils import detect_bos_cos
from indicators import (
    compute_macd_histogram as calculate_macd_histogram,
    compute_rsi as calculate_rsi,
    compute_ma as calculate_ma,
    compute_fvg as calculate_fvg_zones,
    compute_atr,
    find_pivots
)
from chart_generator import generate_chart

def is_fvg_valid(df: pd.DataFrame, direction: str) -> (bool, tuple):
    fvg = calculate_fvg_zones(df)
    upper = fvg["fvg_upper"].iloc[-1]
    lower = fvg["fvg_lower"].iloc[-1]
    price = df["close"].iloc[-1]
    valid = price > lower if direction == "long" else price < upper
    return valid, (upper, lower)

def detect_choch(df: pd.DataFrame, direction: str) -> bool:
    if len(df) < 5:
        return False
    highs = df["high"].rolling(5).max()
    lows = df["low"].rolling(5).min()
    if direction == "long":
        return df["low"].iloc[-1] > lows.iloc[-2] and df["high"].iloc[-1] > highs.iloc[-2]
    else:
        return df["high"].iloc[-1] < highs.iloc[-2] and df["low"].iloc[-1] < lows.iloc[-2]

def analyze_signal(df, direction="long", btc_df=None, total_df=None, btc_d_df=None):
    df_1h = df.copy()
    df_1h.name = df.name
    df_4h = df_1h.copy()
    df_4h.index = pd.to_datetime(df_4h.index, errors='coerce')
    df_4h = df_4h.dropna()
    df_4h = df_4h.resample("4h").agg({
        "open": "first", "high": "max", "low": "min", "close": "last", "volume": "sum"
    }).dropna()

    symbol = df.name
    if "close" not in df_1h.columns:
        print(f"[{symbol}] ❌ Erreur : colonne 'close' absente")
        return None

    entry = df_1h["close"].iloc[-1]
    fvg_valid, fvg_zone = is_fvg_valid(df_1h, direction)
    ote_valid, ote_zone = is_ote(entry, df_1h, direction, return_zone=True)
    ma200_ok = is_ma200_valid(df_1h, direction)
    bos_ok, cos_ok = detect_bos_cos(df_1h, direction)
    choch_ok = detect_choch(df_1h, direction)
    macd_ok, macd_value = is_macd_valid(df_1h, direction)
    macro_ok = is_macro_valid(btc_df, total_df, btc_d_df, direction)
    candle_ok = is_valid_candle(df_1h, direction)
    volume_ok = is_volume_valid(df_1h)
    confirm_ok = is_confirmed_on_4h(df_4h, direction)
    atr_ok = is_atr_valid(df_1h)
    sl, tp1, tp2, rr1, rr2 = calculate_sl_tp_dynamic(df_1h, entry, direction)

    rejected = []
    tolerated = []
    score = 10

    if not fvg_valid: rejected.append("FVG"); score -= 2
    if not bos_ok: rejected.append("BOS"); score -= 1
    if not cos_ok: rejected.append("COS"); score -= 1
    if not choch_ok: rejected.append("CHoCH"); score -= 1
    if not ma200_ok: rejected.append("MA200"); score -= 1
    if not macd_ok: rejected.append("MACD"); score -= 1
    if not macro_ok: rejected.append("MACRO"); score -= 1
    if not confirm_ok: rejected.append("CONFIRM 4H"); score -= 1
    if not candle_ok: rejected.append("Bougie"); score -= 1
    if not volume_ok: rejected.append("Volume"); score -= 1
    if not atr_ok: rejected.append("ATR"); score -= 1

    tolere_ote = False
    if not ote_valid:
        tolerated.append("OTE")
        tolere_ote = True

    print(f"[{symbol}] ➡️ Analyse {direction.upper()} (timeframe = 1H)")
    print(f"[{symbol}]   Entry        : {entry:.4f}")
    print(f"[{symbol}]   OTE valid    : {ote_valid}")
    print(f"[{symbol}]   FVG valid    : {fvg_valid}")
    print(f"[{symbol}]   BOS valid    : {bos_ok}")
    print(f"[{symbol}]   COS valid    : {cos_ok}")
    print(f"[{symbol}]   CHoCH valid  : {choch_ok}")
    print(f"[{symbol}]   MA200 trend  : {ma200_ok}")
    print(f"[{symbol}]   MACD histo   : {macd_value:.5f}")
    print(f"[{symbol}]   MACRO        : {'✅' if macro_ok else '❌'}")
    print(f"[{symbol}]   CONFIRM 4H   : {'✅' if confirm_ok else '❌'}")
    print(f"[{symbol}]   Bougie valide : {candle_ok}")
    print(f"[{symbol}]   Volume OK     : {volume_ok} (actuel: {df_1h['volume'].iloc[-1]:.2f} / moy: {df_1h['volume'].rolling(20).mean().iloc[-1]:.2f})")
    print(f"[{symbol}]   ATR OK        : {atr_ok}")
    print(f"[{symbol}]   Score qualité : {score}/10")

    if score < 8:
        print(f"[{symbol}] ❌ Rejeté (score qualité insuffisant)\n")
        return None

    chart_path = generate_chart(df_1h, symbol, ote_zone, fvg_zone, entry, sl, tp1, direction.upper())
    ideal_msg = "✅ L’entrée actuelle est dans la zone idéale OTE + FVG." if ote_valid and fvg_valid else f"⚠️ Entrée idéale : zone OTE+FVG entre {min(ote_zone):.4f} et {max(ote_zone):.4f}"

    return {
        "symbol": symbol,
        "direction": direction.upper(),
        "entry": entry,
        "sl": sl,
        "tp1": tp1,
        "tp2": tp2,
        "rr1": rr1,
        "rr2": rr2,
        "score": score,
        "chart": chart_path,
        "toleres": tolerated,
        "rejetes": rejected,
        "tolere_ote": tolere_ote,
        "ideal_msg": ideal_msg
    }

def is_ote(price, df, direction, return_zone=False):
    high = df["high"].rolling(20).max().iloc[-1]
    low = df["low"].rolling(20).min().iloc[-1]
    fib_618 = low + 0.618 * (high - low)
    fib_705 = low + 0.705 * (high - low)
    if direction == "long":
        valid = fib_618 <= price <= fib_705
        zone = (fib_705, fib_618)
    else:
        valid = fib_705 <= price <= fib_618
        zone = (fib_618, fib_705)
    return (valid, zone) if return_zone else valid

def is_ma200_valid(df, direction):
    ma200 = calculate_ma(df, period=200)
    current_price = df["close"].iloc[-1]
    return current_price > ma200.iloc[-1] if direction == "long" else current_price < ma200.iloc[-1]

def is_volume_valid(df):
    avg_volume = df["volume"].rolling(20).mean().iloc[-1]
    current_volume = df["volume"].iloc[-1]
    return current_volume > avg_volume * 1.2

def is_valid_candle(df, direction):
    body = abs(df["close"].iloc[-1] - df["open"].iloc[-1])
    wick = df["high"].iloc[-1] - df["low"].iloc[-1]
    return body > wick * 0.4

def is_macd_valid(df, direction):
    hist = calculate_macd_histogram(df["close"])
    value = hist.iloc[-1]
    return (value > 0, value) if direction == "long" else (value < 0, value)

def is_confirmed_on_4h(df_4h, direction):
    ma200 = calculate_ma(df_4h, period=200)
    close = df_4h["close"].iloc[-1]
    return close > ma200.iloc[-1] if direction == "long" else close < ma200.iloc[-1]

def is_macro_valid(btc_df, total_df, btc_d_df, direction):
    btc_trend = btc_df["close"].iloc[-1] > btc_df["close"].iloc[-5]
    total_trend = total_df["close"].iloc[-1] > total_df["close"].iloc[-5]
    return btc_trend and total_trend if direction == "long" else not btc_trend and not total_trend

def is_atr_valid(df, threshold=0.5):
    atr = compute_atr(df).iloc[-1]
    return atr > threshold

def calculate_sl_tp_dynamic(df, entry, direction):
    highs, lows = find_pivots(df)
    if direction == "long" and lows:
        sl = df["low"].iloc[lows[-1]]
        tp1 = entry + (entry - sl)
        tp2 = entry + 2 * (entry - sl)
    elif direction == "short" and highs:
        sl = df["high"].iloc[highs[-1]]
        tp1 = entry - (sl - entry)
        tp2 = entry - 2 * (sl - entry)
    else:
        atr = compute_atr(df).iloc[-1]
        sl = entry - atr * 0.5 if direction == "long" else entry + atr * 0.5
        tp1 = entry + atr if direction == "long" else entry - atr
        tp2 = entry + atr * 2 if direction == "long" else entry - atr * 2
    rr1 = round(abs(tp1 - entry) / abs(entry - sl), 2)
    rr2 = round(abs(tp2 - entry) / abs(entry - sl), 2)
    return round(sl, 4), round(tp1, 4), round(tp2, 4), rr1, rr2
