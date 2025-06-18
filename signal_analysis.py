import numpy as np
import pandas as pd
from structure_utils import detect_bos_cos, detect_choch
from indicators import (
    compute_macd_histogram,
    compute_ma,
    compute_fvg_zones,
    compute_atr,
    find_pivots
)
from chart_generator import generate_chart

def is_ote(entry, df, direction):
    fib_high = df["high"].iloc[-20:-1].max()
    fib_low = df["low"].iloc[-20:-1].min()

    if direction == "long":
        ote_top = fib_low + 0.618 * (fib_high - fib_low)
        ote_bottom = fib_low + 0.705 * (fib_high - fib_low)
    else:
        ote_top = fib_high - 0.705 * (fib_high - fib_low)
        ote_bottom = fib_high - 0.618 * (fib_high - fib_low)

    in_zone = ote_bottom <= entry <= ote_top
    return in_zone, (min(ote_top, ote_bottom), max(ote_top, ote_bottom))

def is_fvg_valid(df, direction):
    fvg = compute_fvg_zones(df)
    if not fvg:
        return False, (0, 0)
    zone = fvg[-1]
    price = df["close"].iloc[-1]
    if direction == "long" and price > zone[0]:
        return True, zone
    elif direction == "short" and price < zone[1]:
        return True, zone
    return False, zone

def is_ma200_valid(df, direction):
    ma200 = compute_ma(df, period=200)
    price = df["close"].iloc[-1]
    if direction == "long":
        return price > ma200.iloc[-1]
    else:
        return price < ma200.iloc[-1]

def is_macd_valid(df, direction):
    hist = compute_macd_histogram(df)
    value = hist.iloc[-1]
    if direction == "long":
        return value > 0, value
    else:
        return value < 0, value

def is_macro_valid(btc_df, total_df, direction):
    try:
        btc_slope = btc_df["close"].iloc[-1] - btc_df["close"].iloc[-10]
        total_slope = total_df["close"].iloc[-1] - total_df["close"].iloc[-10]

        if direction == "long":
            return btc_slope > 0 and total_slope > 0
        else:
            return btc_slope < 0 and total_slope < 0
    except:
        return False

def is_confirmed_on_4h(df, direction):
    last_candle = df.iloc[-1]
    body = abs(last_candle["close"] - last_candle["open"])
    range_candle = last_candle["high"] - last_candle["low"]
    body_ratio = body / range_candle if range_candle != 0 else 0

    if direction == "long":
        return last_candle["close"] > last_candle["open"] and body_ratio > 0.5
    else:
        return last_candle["close"] < last_candle["open"] and body_ratio > 0.5

def is_valid_candle(df, direction):
    last = df.iloc[-1]
    if direction == "long":
        return last["close"] > last["open"]
    else:
        return last["close"] < last["open"]

def is_volume_valid(df):
    recent_vol = df["volume"].iloc[-1]
    avg_vol = df["volume"].iloc[-20:-1].mean()
    return recent_vol > avg_vol * 1.2

def is_atr_valid(df):
    atr = compute_atr(df)
    return atr.iloc[-1] > 0.01

def calculate_sl_tp_dynamic(df, entry, direction):
    atr = compute_atr(df).iloc[-1]
    sl_buffer = atr * 1.5
    tp1_buffer = atr * 3
    tp2_buffer = atr * 5

    if direction == "long":
        sl = entry - sl_buffer
        tp1 = entry + tp1_buffer
        tp2 = entry + tp2_buffer
    else:
        sl = entry + sl_buffer
        tp1 = entry - tp1_buffer
        tp2 = entry - tp2_buffer

    rr1 = round(abs(tp1 - entry) / abs(entry - sl), 2)
    rr2 = round(abs(tp2 - entry) / abs(entry - sl), 2)
    return round(sl, 4), round(tp1, 4), round(tp2, 4), rr1, rr2

def analyze_signal(df, direction="long", btc_df=None, total_df=None, btc_d_df=None):
    df_1h = df.copy()
    df_1h.name = df.name
    symbol = df.name
    entry = df_1h["close"].iloc[-1]

    # Convertir et resampler en 4H
    df_4h = df_1h.copy()
    df_4h.index = pd.to_datetime(df_4h.index, errors='coerce')
    df_4h = df_4h.dropna(subset=["open", "high", "low", "close", "volume"])
    df_4h = df_4h.resample("4H").agg({
        "open": "first", "high": "max", "low": "min", "close": "last", "volume": "sum"
    }).dropna()

    # Calculs indicateurs
    fvg_valid, fvg_zone = is_fvg_valid(df_1h, direction)
    ote_valid, ote_zone = is_ote(entry, df_1h, direction)
    ma200_ok = is_ma200_valid(df_1h, direction)
    bos_ok, cos_ok = detect_bos_cos(df_1h, direction)
    choch_ok = detect_choch(df_1h, direction)
    macd_ok, macd_val = is_macd_valid(df_1h, direction)
    macro_ok = is_macro_valid(btc_df, total_df, direction)
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
    if not candle_ok: rejected.append("BOUGIE"); score -= 1
    if not volume_ok: rejected.append("VOLUME"); score -= 1
    if not atr_ok: rejected.append("ATR"); score -= 1

    tolere_ote = False
    if not ote_valid:
        tolerated.append("OTE")
        tolere_ote = True

    print(f"[{symbol}] ➡️ Analyse {direction.upper()}")
    print(f"[{symbol}]   Entry        : {entry:.4f}")
    print(f"[{symbol}]   OTE valid    : {ote_valid}")
    print(f"[{symbol}]   FVG valid    : {fvg_valid}")
    print(f"[{symbol}]   BOS valid    : {bos_ok}")
    print(f"[{symbol}]   COS valid    : {cos_ok}")
    print(f"[{symbol}]   CHoCH valid  : {choch_ok}")
    print(f"[{symbol}]   MA200 trend  : {ma200_ok}")
    print(f"[{symbol}]   MACD histo   : {macd_val:.5f}")
    print(f"[{symbol}]   CONFIRM 4H   : {confirm_ok}")
    print(f"[{symbol}]   VOLUME OK    : {volume_ok}")
    print(f"[{symbol}]   ATR OK       : {atr_ok}")
    print(f"[{symbol}]   Score qualité : {score}/10")

    if score < 8:
        print(f"[{symbol}] ❌ Rejeté (score qualité insuffisant)\n")
        return None

    chart_path = generate_chart(df_1h, symbol, ote_zone, fvg_zone, entry, sl, tp1, direction.upper())

    comment = f"📌 Zone idéale d'entrée :\nOTE = {ote_zone[0]:.4f} → {ote_zone[1]:.4f}\nFVG = {fvg_zone[0]:.4f} → {fvg_zone[1]:.4f}"

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
        "comment": comment
    }
