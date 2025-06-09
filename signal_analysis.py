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


def analyze_macro(btc_df: pd.DataFrame, total_df: pd.DataFrame, direction: str) -> (bool, list):
    rejected = []
    try:
        btc_trend = btc_df['close'].iloc[-1] > btc_df['close'].iloc[-20]
        total_trend = total_df['close'].iloc[-1] > total_df['close'].iloc[-20]
        btc_range = abs(btc_df['close'].iloc[-1] - btc_df['close'].iloc[-20]) / btc_df['close'].iloc[-20] < 0.01
        total_range = abs(total_df['close'].iloc[-1] - total_df['close'].iloc[-20]) / total_df['close'].iloc[-20] < 0.01

        if btc_range and total_range:
            rejected.append("MACRO (BTC et TOTAL en range)")
        elif direction == "long":
            if not btc_trend or not total_trend:
                rejected.append("MACRO (BTC ou TOTAL baissier)")
        else:
            if btc_trend or total_trend:
                rejected.append("MACRO (BTC ou TOTAL haussier)")
    except Exception as e:
        print(f"⚠️ Erreur analyse macro : {e}")
        rejected.append("MACRO (indéterminé)")

    return len(rejected) == 0, rejected


def detect_fakeout(df, direction):
    if direction == "long":
        return df['low'].iloc[-1] < df['low'].rolling(window=20).min().iloc[-2]
    else:
        return df['high'].iloc[-1] > df['high'].rolling(window=20).max().iloc[-2]


def analyze_signal(df, direction="long", btc_df=None, total_df=None):
    symbol = getattr(df, 'name', 'UNKNOWN')
    dir_up = direction.lower() == "long"
    dir_str = direction.upper()

    print(f"[{symbol}] ➡️ Analyse {dir_str}")

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
    fakeout_ok = detect_fakeout(df, direction)

    print(f"[{symbol}]   Entry        : {entry:.4f}")
    print(f"[{symbol}]   OTE valid    : {in_ote}")
    print(f"[{symbol}]   FVG valid    : {in_fvg}")
    print(f"[{symbol}]   BOS valid    : {bos_ok}")
    print(f"[{symbol}]   COS valid    : {cos_ok}")
    print(f"[{symbol}]   MA200 trend  : {ma_ok}")
    print(f"[{symbol}]   MACD histo   : {macd_hist:.5f}")
    print(f"[{symbol}]   Fakeout ok   : {fakeout_ok}")
    print(f"[{symbol}]   MACRO        : {'OK' if macro_ok else '❌'}")

    last_open = df['open'].iloc[-1]
    last_close = df['close'].iloc[-1]
    last_volume = df['volume'].iloc[-1]
    avg_volume = df['volume'].rolling(window=20).mean().iloc[-1]
    bougie_valide = (last_close > last_open) if dir_up else (last_close < last_open)

    print(f"[{symbol}]   Bougie valide : {bougie_valide}")

    if not bougie_valide:
        print(f"[{symbol}] ❌ Rejeté (bougie invalide)
")
        return None

    score = 0
    if in_ote: score += 1
    if in_fvg: score += 1
    if bos_ok: score += 2
    if cos_ok: score += 2
    if ma_ok: score += 1
    if last_volume >= avg_volume: score += 1
    if macd_ok: score += 1
    if macro_ok: score += 1
    if fakeout_ok: score += 1

    print(f"[{symbol}]   Volume OK     : {last_volume >= avg_volume} (actuel: {last_volume:.2f} / moy: {avg_volume:.2f})")
    print(f"[{symbol}]   Score qualité : {score}/10")

    checks = {
        "FVG": in_fvg,
        "BOS": bos_ok,
        "COS": cos_ok,
        "MA200": ma_ok,
        "MACD": macd_ok,
        "VOLUME": last_volume >= avg_volume,
        "MACRO": macro_ok,
        "FAKEOUT": fakeout_ok
    }

    failed = [k for k, v in checks.items() if not v]
    tolerated = []

    if score < 8:
        print(f"[{symbol}] ❌ Rejeté (score qualité insuffisant)
")
        return None

    if failed == [] or failed == ["OTE"]:
        if "OTE" in failed:
            tolerated = ["OTE"]
            print(f"[{symbol}] ⚠️ Tolérance activée pour : OTE")
    else:
        print(f"[{symbol}] ❌ Rejeté ({', '.join(failed + macro_reject)})
")
        return None

    if "OTE" in tolerated or in_ote:
        entry = ote.get("ote_618", entry)
        print(f"[{symbol}] ✅ Entrée optimisée (fib 0.618) : {entry:.4f}")

    if dir_up and lows:
        pivot = df['low'].iloc[lows[-1]]
        sl = pivot - atr
    elif not dir_up and highs:
        pivot = df['high'].iloc[highs[-1]]
        sl = pivot + atr
    else:
        sl = df['low'].iloc[-1] - atr if dir_up else df['high'].iloc[-1] + atr

    pivots = highs if dir_up else lows
    tp1 = None
    for i in reversed(pivots):
        level = df['high'].iloc[i] if dir_up else df['low'].iloc[i]
        rr = (level - entry) / (entry - sl) if dir_up else (entry - level) / (sl - entry)
        if rr >= 1.5:
            tp1 = level - atr * 0.2 if dir_up else level + atr * 0.2
            break

    if tp1 is None:
        for i in reversed(pivots):
            level = df['high'].iloc[i] if dir_up else df['low'].iloc[i]
            rr = (level - entry) / (entry - sl) if dir_up else (entry - level) / (sl - entry)
            if rr >= 1.2:
                tp1 = level - atr * 0.2 if dir_up else level + atr * 0.2
                break

    if tp1 is None:
        risk = abs(entry - sl)
        tp1 = entry + 1.2 * risk if dir_up else entry - 1.2 * risk

    extension = abs(tp1 - entry)
    tp2 = tp1 + extension if dir_up else tp1 - extension

    risk = abs(entry - sl)
    rr1 = round(abs(tp1 - entry) / risk, 2)
    rr2 = round(abs(tp2 - entry) / risk, 2)

    commentaire = f"🎯 Confirmé swing pro (score={score}/10, RR1={rr1}, tolérance={','.join(tolerated) if tolerated else 'Aucune'})"
    if "OTE" in tolerated:
        commentaire += "\n📌 Entrée optimisée sur fib 0.618 (OTE)"

    return {
        'symbol': symbol,
        'entry': entry,
        'sl': sl,
        'tp1': tp1,
        'tp2': tp2,
        'rr1': rr1,
        'rr2': rr2,
        'direction': "LONG" if dir_up else "SHORT",
        'type': "CONFIRMÉ",
        'score': score,
        'comment': commentaire,
        'tolere_ote': "OTE" in tolerated,
        'toleres': tolerated,
        'rejetes': failed + macro_reject if len(failed + macro_reject) > 1 else []
    }
