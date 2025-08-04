import numpy as np
import pandas as pd
from indicators import (
    calculate_ma,
    calculate_macd_histogram,
    calculate_rsi,
    calculate_atr,
    detect_fvg,
    detect_ote_zone,
    is_price_in_ote_zone,
    detect_divergence,
)
from structure_utils import detect_bos, detect_cos, detect_choch
from kucoin_utils import get_klines

TRADE_AMOUNT = 20     # 💰 Montant par trade
TRADE_LEVERAGE = 3    # 📈 Levier

def confirm_4h_trend(symbol, direction):
    df_4h = get_klines(symbol, interval='4hour', limit=100)
    if df_4h is None or len(df_4h) < 50:
        return False

    df_4h = calculate_ma(df_4h, 200)
    last_close = df_4h['close'].iloc[-1]
    ma200 = df_4h['ma_200'].iloc[-1]

    return last_close > ma200 if direction == 'long' else last_close < ma200

def analyze_signal(df, symbol, direction):
    if df is None or df.empty or 'timestamp' not in df.columns:
        return None
    if 'volume' not in df.columns or df['volume'].isnull().any():
        return None

    df = calculate_ma(df, 200)
    df['macd_histogram'] = calculate_macd_histogram(df)
    df['rsi'] = calculate_rsi(df)
    df['atr'] = calculate_atr(df)

    fvg_zone = detect_fvg(df, direction)
    ote_zone = detect_ote_zone(df, direction)

    bos = detect_bos(df, direction)
    cos = detect_cos(df, direction)
    choch = detect_choch(df, direction)
    divergence = detect_divergence(df)
    confirmation_4h = confirm_4h_trend(symbol, direction)

    current_price = df['close'].iloc[-1]
    volume_ok = df['volume'].iloc[-1] > df['volume'].rolling(20).mean().iloc[-1] * 1.2
    macd_ok = df['macd_histogram'].iloc[-1] > 0 if direction == 'long' else df['macd_histogram'].iloc[-1] < 0
    ma_ok = current_price > df['ma_200'].iloc[-1] if direction == 'long' else current_price < df['ma_200'].iloc[-1]
    in_ote = is_price_in_ote_zone(df, ote_zone)
    in_fvg = fvg_zone is not None and fvg_zone[0] <= current_price <= fvg_zone[1]

    atr = df['atr'].iloc[-1]
    sl = current_price - atr if direction == 'long' else current_price + atr
    tp = current_price + 2 * atr if direction == 'long' else current_price - 2 * atr

    # Score pondéré
    score = 0
    logs = []

    if fvg_zone: score += 1
    else: logs.append("❌ FVG")

    if in_ote: score += 1
    else: logs.append("⚠️ OTE")

    if ma_ok: score += 1
    else: logs.append("❌ MA200")

    if macd_ok: score += 1
    else: logs.append("❌ MACD")

    if bos: score += 1
    else: logs.append("❌ BOS")

    if cos: score += 1
    else: logs.append("❌ COS")

    if choch: score += 1
    else: logs.append("❌ CHoCH")

    if divergence: score += 1
    else: logs.append("⚠️ Divergence")

    if volume_ok: score += 1
    else: logs.append("❌ Volume")

    if confirmation_4h: score += 1
    else: logs.append("❌ 4H")

    if score < 8 or not in_ote or not in_fvg:
        print(f"[{symbol.upper()} - {direction.upper()}] ❌ Rejeté | Score: {score}/10 | {', '.join(logs)}")
        return None

    comment = (
        f"{symbol.upper()} ({direction.upper()})\n"
        f"Score: {score}/10\n"
        f"Entrée idéale : {round(current_price, 4)}\n"
        f"SL: {round(sl, 4)}\n"
        f"TP: {round(tp, 4)}\n"
        f"{', '.join(logs)}"
    )

    print(f"[{symbol.upper()} - {direction.upper()}] ✅ Signal VALIDE | Score: {score}/10")

    return {
        "valide": True,
        "symbol": symbol,
        "direction": direction,
        "entry": round(current_price, 4),
        "sl": round(sl, 4),
        "tp": round(tp, 4),
        "commentaire": comment,
        "score": score,
        "amount": TRADE_AMOUNT,
        "leverage": TRADE_LEVERAGE,
    }
