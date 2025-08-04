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

    # --- Score pondéré expert ---
    score = 0
    total_possible = 0
    logs = []

    # FVG (2 pts)
    total_possible += 2
    if in_fvg:
        score += 2
    else:
        logs.append("❌ FVG")

    # MA200 (2 pts)
    total_possible += 2
    if ma_ok:
        score += 2
    else:
        logs.append("❌ MA200")

    # MACD (1.5 pts)
    total_possible += 1.5
    if macd_ok:
        score += 1.5
    else:
        logs.append("❌ MACD")

    # Volume (1.5 pts)
    total_possible += 1.5
    if volume_ok:
        score += 1.5
    else:
        logs.append("❌ Volume")

    # BOS / COS / CHoCH (1 pt chacun)
    for name, valid in [("BOS", bos), ("COS", cos), ("CHoCH", choch)]:
        total_possible += 1
        if valid:
            score += 1
        else:
            logs.append(f"❌ {name}")

    # Divergence (0.5 pt)
    total_possible += 0.5
    if divergence:
        score += 0.5
    else:
        logs.append("⚠️ Divergence")

    # Confirmation 4H (1.5 pts)
    total_possible += 1.5
    if confirmation_4h:
        score += 1.5
    else:
        logs.append("❌ 4H")

    # OTE = toléré, max -0.5 pt
    if not in_ote:
        logs.append("⚠️ Hors OTE")
        score -= 0.5

    # --- Seuil d’acceptation : score ≥ 8 ---
    if score < 8 or not in_fvg:
        print(f"[{symbol.upper()} - {direction.upper()}] ❌ Rejeté | Score: {round(score, 1)}/{round(total_possible, 1)} | {', '.join(logs)}")
        return None

    comment = (
        f"{symbol.upper()} ({direction.upper()})\n"
        f"Score: {round(score, 1)}/{round(total_possible, 1)}\n"
        f"Entrée idéale : {round(current_price, 4)}\n"
        f"SL: {round(sl, 4)} | TP: {round(tp, 4)}\n"
        f"{', '.join(logs)}"
    )

    print(f"[{symbol.upper()} - {direction.upper()}] ✅ Signal VALIDE | Score: {round(score, 1)}/{round(total_possible, 1)}")

    return {
        "valide": True,
        "symbol": symbol,
        "direction": direction,
        "entry": round(current_price, 4),
        "sl": round(sl, 4),
        "tp": round(tp, 4),
        "commentaire": comment,
        "score": round(score, 1),
        "amount": TRADE_AMOUNT,
        "leverage": TRADE_LEVERAGE,
    }
