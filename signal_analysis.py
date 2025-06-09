from kucoin_utils import fetch_klines
from indicators import (
    compute_atr,
    compute_ote,
    compute_fvg,
    compute_ma,
    find_pivots,
    compute_macd_histogram
)
from structure_utils import (
    is_cos_valid,
    is_bos_valid
)
import pandas as pd


def is_range(df: pd.DataFrame, seuil: float = 0.02) -> bool:
    closes = df['close'].iloc[-20:]
    variation = (max(closes) - min(closes)) / closes.mean()
    return variation < seuil


def analyze_macro(btc_df: pd.DataFrame, total_df: pd.DataFrame, direction: str) -> (bool, list):
    rejected = []

    try:
        btc_trend = btc_df['close'].iloc[-1] > btc_df['close'].iloc[-20]
        total_trend = total_df['close'].iloc[-1] > total_df['close'].iloc[-20]
        btc_range = is_range(btc_df)

        if direction == "long":
            if not btc_trend or not total_trend or btc_range:
                rejected.append("MACRO (BTC ou TOTAL baissier ou en range)")
        else:
            if btc_trend or total_trend or btc_range:
                rejected.append("MACRO (BTC ou TOTAL haussier ou en range)")
    except Exception as e:
        print(f"⚠️ Erreur analyse macro : {e}")
        rejected.append("MACRO (indéterminé)")

    return len(rejected) == 0, rejected


def has_fakeout(df, direction):
    last = df.iloc[-1]
    prev = df.iloc[-2]
    if direction == "long":
        return last['low'] < prev['low'] and last['close'] > prev['close']
    else:
        return last['high'] > prev['high'] and last['close'] < prev['close']


def analyze_signal(df_1h, direction="long", btc_df=None, total_df=None):
    symbol = getattr(df_1h, 'name', 'UNKNOWN')
    dir_up = direction == "long"
    dir_str = direction.upper()

    # Confirmation 4H
    try:
        df_4h = fetch_klines(symbol, interval="4h")
        df_4h.name = symbol
    except Exception as e:
        print(f"[{symbol}] ⚠️ Erreur fetch 4H : {e}")
        return None

    print(f"[{symbol}] ➡️ Analyse {dir_str}")

    df = df_1h
    atr = compute_atr(df).iloc[-1]
    ote = compute_ote(df).iloc[-1]
    fvg = compute_fvg(df).iloc[-1]
    ma200 = compute_ma(df, 200).iloc[-1]
    macd_hist = compute_macd_histogram(df).iloc[-1]
    highs, lows = find_pivots(df, window=5)
    entry = df['close'].iloc[-1]

    ote_upper, ote_lower = ote['ote_upper'], ote['ote_lower']
    fvg_upper, fvg_lower = fvg['fvg_upper'], fvg['fvg_lower']
    in_ote = (ote_lower <= entry <= ote_upper)
    in_fvg = (fvg_lower <= entry <= fvg_upper)

    bos_ok = is_bos_valid(df, direction)
    cos_ok = is_cos_valid(df, direction)
    ma_ok = (entry > ma200) if dir_up else (entry < ma200)
    macd_ok = macd_hist > 0 if dir_up else macd_hist < 0
    macro_ok, macro_reject = analyze_macro(btc_df, total_df, direction)
    fakeout = has_fakeout(df, direction)

    # Bougie + volume
    last_open = df['open'].iloc[-1]
    last_close = df['close'].iloc[-1]
    last_volume = df['volume'].iloc[-1]
    avg_volume = df['volume'].rolling(window=20).mean().iloc[-1]
    bougie_valide = (last_close > last_open) if dir_up else (last_close < last_open)
    volume_ok = last_volume >= avg_volume

    # Confirmation 4H = clôture dans la bonne direction + structure valide
    last_4h_open = df_4h['open'].iloc[-1]
    last_4h_close = df_4h['close'].iloc[-1]
    candle_4h_ok = (last_4h_close > last_4h_open) if dir_up else (last_4h_close < last_4h_open)
    bos_4h = is_bos_valid(df_4h, direction)
    cos_4h = is_cos_valid(df_4h, direction)
    tf_confirm = candle_4h_ok and bos_4h and cos_4h

    # Score pondéré (max 10)
    score = 0
    score += 1 if in_ote else 0
    score += 1 if in_fvg else 0
    score += 2 if bos_ok else 0
    score += 2 if cos_ok else 0
    score += 1 if ma_ok else 0
    score += 1 if volume_ok else 0
    score += 1 if macd_ok else 0
    score += 1 if macro_ok else 0

    print(f"[{symbol}] Score : {score}/10 | Bougie : {bougie_valide} | Confirm 4H : {tf_confirm} | Fakeout : {fakeout}")
    if not bougie_valide or not tf_confirm or score < 8:
        print(f"[{symbol}] ❌ Rejeté (bougie, TF, score)")
        return None

    checks = {
        "FVG": in_fvg,
        "BOS": bos_ok,
        "COS": cos_ok,
        "MA200": ma_ok,
        "MACD": macd_ok,
        "VOLUME": volume_ok,
        "MACRO": macro_ok
    }

    failed = [k for k, v in checks.items() if not v]
    tolerated = []
    if failed and failed != ["OTE"]:
        print(f"[{symbol}] ❌ Rejeté ({', '.join(failed + macro_reject)})")
        return None
    if "OTE" in failed:
        tolerated.append("OTE")

    if in_ote or "OTE" in tolerated:
        entry = ote.get("ote_618", entry)

    # SL dynamique
    if dir_up and lows:
        pivot = df['low'].iloc[lows[-1]]
        sl = pivot - atr
    elif not dir_up and highs:
        pivot = df['high'].iloc[highs[-1]]
        sl = pivot + atr
    else:
        sl = df['low'].iloc[-1] - atr if dir_up else df['high'].iloc[-1] + atr

    # TP intelligent
    pivots = highs if dir_up else lows
    tp1 = None
    for i in reversed(pivots):
        level = df['high'].iloc[i] if dir_up else df['low'].iloc[i]
        rr = (level - entry) / (entry - sl) if dir_up else (entry - level) / (sl - entry)
        if rr >= 1.5:
            tp1 = level - atr * 0.2 if dir_up else level + atr * 0.2
            break
    if tp1 is None:
        risk = abs(entry - sl)
        tp1 = entry + 1.2 * risk if dir_up else entry - 1.2 * risk

    extension = abs(tp1 - entry)
    tp2 = tp1 + extension if dir_up else tp1 - extension
    rr1 = round(abs(tp1 - entry) / abs(entry - sl), 2)
    rr2 = round(abs(tp2 - entry) / abs(entry - sl), 2)

    comment = f"🎯 Confirmé swing pro (score={score}/10, RR1={rr1}, tolérance={','.join(tolerated) if tolerated else 'Aucune'})"
    if "OTE" in tolerated:
        comment += "\n📌 Entrée optimisée sur fib 0.618 (OTE)"
    if fakeout:
        comment += "\n⚠️ Possible fakeout détecté (liquidité prise avant retournement)"

    return {
        'symbol': symbol,
        'entry': entry,
        'sl': sl,
        'tp1': tp1,
        'tp2': tp2,
        'rr1': rr1,
        'rr2': rr2,
        'direction': dir_str,
        'type': "CONFIRMÉ",
        'score': score,
        'comment': comment,
        'tolere_ote': "OTE" in tolerated,
        'toleres': tolerated,
        'rejetes': failed + macro_reject if failed + macro_reject else []
    }
