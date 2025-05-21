# /app/signal_analysis.py

from kucoin_utils import fetch_klines
from indicators import (
    compute_atr,
    compute_ote,
    compute_fvg,
    compute_rsi,
    compute_macd,
    compute_ma,
    find_pivots
)

def is_bos_valid(df, direction):
    highs, lows = find_pivots(df, window=5)
    if direction.lower() == "long":
        if len(highs) < 2:
            return False
        prev_high = df['high'].iloc[highs[-2]]
        return df['close'].iloc[-1] > prev_high
    else:
        if len(lows) < 2:
            return False
        prev_low = df['low'].iloc[lows[-2]]
        return df['close'].iloc[-1] < prev_low

def is_cos_valid(df, direction):
    highs, lows = find_pivots(df, window=5)
    if direction.lower() == "long":
        if len(lows) < 2:
            return False
        prev_low = df['low'].iloc[lows[-2]]
        return df['low'].iloc[-1] < prev_low
    else:
        if len(highs) < 2:
            return False
        prev_high = df['high'].iloc[highs[-2]]
        return df['high'].iloc[-1] > prev_high

def is_btc_favorable():
    btc = fetch_klines("XBTUSDTM")
    if btc is None or len(btc) < 200:
        return False
    ma200 = compute_ma(btc, period=200).iloc[-1]
    return btc['close'].iloc[-1] > ma200

def analyze_signal(df, direction="long"):
    symbol = getattr(df, 'name', 'UNKNOWN')
    dir_up = direction.lower() == "long"
    dir_str = direction.upper()

    print(f"[{symbol}] ➡️ Analyse {dir_str}")

    # 1) calcul des indicateurs
    atr    = compute_atr(df).iloc[-1]
    ote    = compute_ote(df).iloc[-1]
    fvg    = compute_fvg(df).iloc[-1]
    ma200  = compute_ma(df, period=200).iloc[-1]
    highs, lows = find_pivots(df, window=5)
    entry  = df['close'].iloc[-1]

    # zones
    ote_upper, ote_lower = ote['ote_upper'], ote['ote_lower']
    fvg_upper, fvg_lower = fvg['fvg_upper'], fvg['fvg_lower']

    # 2) validations
    bos_ok  = is_bos_valid(df, direction)
    cos_ok  = is_cos_valid(df, direction)
    btc_ok  = is_btc_favorable()
    ma200_ok= (entry > ma200) if dir_up else (entry < ma200)
    ote_ok  = (ote_lower <= entry <= ote_upper)
    fvg_ok  = (fvg_lower <= entry <= fvg_upper)

    print(f"[{symbol}]   BOS valid      : {bos_ok}")
    print(f"[{symbol}]   COS valid      : {cos_ok}")
    print(f"[{symbol}]   BTC favorable  : {btc_ok}")
    print(f"[{symbol}]   MA200 trend    : {ma200_ok}")
    print(f"[{symbol}]   OTE zone valid : {ote_ok} (between {ote_lower:.4f} & {ote_upper:.4f})")
    print(f"[{symbol}]   FVG zone valid : {fvg_ok} (between {fvg_lower:.4f} & {fvg_upper:.4f})")

    checks = {
        "BOS": bos_ok,
        "COS": cos_ok,
        "BTC": btc_ok,
        "MA200": ma200_ok,
        "OTE": ote_ok,
        "FVG": fvg_ok
    }
    failed = [name for name, ok in checks.items() if not ok]

    if failed:
        print(f"[{symbol}] ❌ Signal REJETÉ ({', '.join(failed)})\n")
        return None

    # 3) niveaux initiaux
    if dir_up:
        sl = df['low'].iloc[-1]
        tp = entry + (entry - sl) * 2.5
    else:
        sl = df['high'].iloc[-1]
        tp = entry - (sl - entry) * 2.5

    # 4) ajustements ATR / % entry
    min_sl = atr * 1.5
    max_sl = entry * 0.06
    dist   = abs(entry - sl)
    if dist < min_sl:
        sl = entry + min_sl if not dir_up else entry - min_sl
    if dist > max_sl:
        sl = entry + max_sl if not dir_up else entry - max_sl

    # 5) ancrage pivots (buffer 20% ATR)
    if dir_up and lows:
        pivot = df['low'].iloc[lows[-1]]
        sl = min(sl, pivot - atr * 0.2)
    if not dir_up and highs:
        pivot = df['high'].iloc[highs[-1]]
        sl = max(sl, pivot + atr * 0.2)

    # 6) calcul R:R
    rr = (tp - entry) / (entry - sl) if dir_up else (entry - tp) / (sl - entry)
    rr = round(rr, 2)

    print(f"[{symbol}] ✅ Signal CONFIRMÉ (RR={rr})\n")

    return {
        "type":      "CONFIRMÉ",
        "direction": dir_str,
        "entry":     entry,
        "sl":        sl,
        "tp":        tp,
        "rr":        rr,
        "ote_zone":  (ote_upper, ote_lower),
        "fvg_zone":  (fvg_upper, fvg_lower),
        "ma200":     ma200,
        "symbol":    symbol,
        "comment":   "🎯 Signal confirmé – entrée idéale après repli"
    }
