import numpy as np
import pandas as pd
from indicators import calculate_rsi, calculate_macd_histogram, calculate_ma, calculate_atr, calculate_fvg_zones, detect_divergence
from structure_utils import detect_bos, detect_cos, detect_choch
from chart_generator import generate_chart

# --- Configuration ---

LEVERAGE = 3
TRADE_AMOUNT = 20

# --- Analyse d'un signal ---

def analyze_signal(df, df_4h=None, symbol="", direction="long", context_macro=None):
    if df is None or df.empty or 'timestamp' not in df.columns:
        return None

    df = df.copy()

    # INDICATEURS
    df["rsi"] = calculate_rsi(df)
    df["macd_histogram"] = calculate_macd_histogram(df)
    df["ma200"] = calculate_ma(df)
    df["atr"] = calculate_atr(df)

    fvg_zone = calculate_fvg_zones(df, direction)
    bos = detect_bos(df, direction)
    cos = detect_cos(df, direction)
    choch = detect_choch(df, direction)
    divergence = detect_divergence(df, direction)

    # ZONE OTE (Optimal Trade Entry)
    last_price = df["close"].iloc[-1]
    high = df["high"].rolling(50).max().iloc[-1]
    low = df["low"].rolling(50).min().iloc[-1]

    if direction == "long":
        fib_618 = low + 0.618 * (high - low)
        fib_786 = low + 0.786 * (high - low)
        in_ote = fib_618 <= last_price <= fib_786
    else:
        fib_127 = high - 0.272 * (high - low)
        fib_1618 = high - 0.618 * (high - low)
        in_ote = fib_127 >= last_price >= fib_1618

    ote_zone = (fib_618, fib_786) if direction == "long" else (fib_127, fib_1618)

    # Volume
    volume_mean = df["volume"].rolling(20).mean().iloc[-1]
    volume_last = df["volume"].iloc[-1]
    volume_valid = volume_last > 1.2 * volume_mean

    # Bougie de confirmation
    if direction == "long":
        candle_valid = df["close"].iloc[-1] > df["open"].iloc[-1]
    else:
        candle_valid = df["close"].iloc[-1] < df["open"].iloc[-1]

    # MACRO : TOTAL et BTC.D
    macro_valid = True
    macro_comment = ""
    if context_macro:
        total = context_macro.get("TOTAL")
        btcd = context_macro.get("BTC.D")
        if direction == "long" and (not total or total["trend"] != "up"):
            macro_valid = False
            macro_comment = "❌ TOTAL baissier"
        elif direction == "short" and (not total or total["trend"] != "down"):
            macro_valid = False
            macro_comment = "❌ TOTAL haussier"

    # Confirmation 4H
    confirmation_4h = True
    if df_4h is not None and not df_4h.empty:
        df_4h["ma200"] = calculate_ma(df_4h)
        df_4h["volume"] = df_4h["volume"]
        price_4h = df_4h["close"].iloc[-1]
        ma200_4h = df_4h["ma200"].iloc[-1]
        volume_4h = df_4h["volume"].iloc[-1]
        volume_mean_4h = df_4h["volume"].rolling(20).mean().iloc[-1]

        if direction == "long":
            confirmation_4h = price_4h > ma200_4h and volume_4h > volume_mean_4h
        else:
            confirmation_4h = price_4h < ma200_4h and volume_4h > volume_mean_4h

    # --- Score pondéré ---
    valid = []
    toleres = []
    rejetes = []

    def check(val, name, tolerable=False):
        if val:
            valid.append(name)
        elif tolerable:
            toleres.append(name)
        else:
            rejetes.append(name)

    check(bos, "BOS")
    check(cos, "COS")
    check(choch, "CHoCH")
    check(divergence, "DIVERGENCE", tolerable=True)
    check(volume_valid, "VOLUME")
    check(candle_valid, "BOUGIE", tolerable=True)
    check(macro_valid, "MACRO")
    check(confirmation_4h, "CONFIRMATION 4H")
    check(in_ote, "OTE", tolerable=True)
    check(fvg_zone is not None, "FVG")

    score = len(valid) + 0.5 * len(toleres)

    if score < 8 or len(rejetes) > 0:
        return {
            "valid": False,
            "score": score,
            "rejetes": rejetes,
            "toleres": toleres,
            "comment": f"⛔ Rejeté : {', '.join(rejetes)}"
        }

    # SL = ATR * 1.5
    sl = last_price - df["atr"].iloc[-1] * 1.5 if direction == "long" else last_price + df["atr"].iloc[-1] * 1.5
    rr = 2
    tp = last_price + (last_price - sl) * rr if direction == "long" else last_price - (sl - last_price) * rr

    # Chart (désactivé si inutilisé)
    # generate_chart(df, symbol, ote_zone, fvg_zone, last_price, sl, tp, direction)

    comment = f"""
📈 *{symbol}* — *{direction.upper()}*
🎯 Entrée : `{last_price:.4f}`
🎯 SL : `{sl:.4f}`
🎯 TP : `{tp:.4f}` (R:R {rr})
⚙️ Levier : {LEVERAGE}x
💰 Taille : {TRADE_AMOUNT} USDT

❌ Rejetés : {', '.join(rejetes) if rejetes else 'Aucun'}
⚠️ Tolérés : {', '.join(toleres) if toleres else 'Aucun'}
"""

    return {
        "valid": True,
        "score": score,
        "entry": last_price,
        "sl": sl,
        "tp": tp,
        "leverage": LEVERAGE,
        "amount": TRADE_AMOUNT,
        "direction": direction,
        "symbol": symbol,
        "comment": comment
    }
