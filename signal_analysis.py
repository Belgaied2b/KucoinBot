import numpy as np
import pandas as pd
from structure_utils import detect_bos_cos
from indicators import compute_macd_histogram as calculate_macd_histogram
from indicators import compute_rsi as calculate_rsi
from indicators import compute_ma as calculate_ma
from indicators import compute_fvg as calculate_fvg_zones
from chart_generator import generate_chart

def analyze_signal(df, direction="long", btc_df=None, total_df=None, btc_d_df=None):
    df_1h = df.copy()
    df_1h.name = df.name
    timeframe = "1H"

    df_4h = df_1h.copy()
    df_4h.index = pd.to_datetime(df_4h.index, errors='coerce')
    df_4h = df_4h.dropna(subset=["open", "high", "low", "close", "volume"])
    df_4h = df_4h.resample("4h").agg({
        "open": "first",
        "high": "max",
        "low": "min",
        "close": "last",
        "volume": "sum"
    }).dropna()

    symbol = df.name
    if "close" not in df_1h.columns:
        print(f"[{symbol}] ❌ Erreur : colonne 'close' absente")
        return None

    entry = df_1h["close"].iloc[-1]

    fvg_valid = is_fvg_valid(df_1h, direction)
    ote_valid = is_ote(entry, df_1h, direction)
    ma200_ok = is_ma200_valid(df_1h, direction)
    bos_ok, cos_ok = detect_bos_cos(df_1h, direction)
    macd_ok, macd_value = is_macd_valid(df_1h, direction)
    macro_ok, btc_trend, total_trend, btc_d_trend = is_macro_valid(btc_df, total_df, btc_d_df, direction)
    candle_ok = is_valid_candle(df_1h, direction)
    volume_ok = is_volume_valid(df_1h)
    confirm_ok = is_confirmed_on_4h(df_4h, direction)

    sl, tp1, tp2, rr1, rr2 = calculate_sl_tp(df_1h, entry, direction)
    rejected = []
    tolerated = []
    score = 10

    if not fvg_valid: rejected.append("FVG"); score -= 2
    if not bos_ok: rejected.append("BOS"); score -= 1
    if not cos_ok: rejected.append("COS"); score -= 1
    if not ma200_ok: rejected.append("MA200"); score -= 1
    if not macd_ok: rejected.append("MACD"); score -= 1
    if not macro_ok: rejected.append("MACRO"); score -= 1
    if not confirm_ok: rejected.append("CONFIRM 4H"); score -= 1
    if not candle_ok: rejected.append("Bougie"); score -= 1
    if not volume_ok: rejected.append("Volume"); score -= 1

    tolere_ote = False
    if not ote_valid:
        tolerated.append("OTE")
        tolere_ote = True

    print(f"[{symbol}] ➡️ Analyse {direction.upper()} (timeframe = {timeframe})")
    print(f"[{symbol}]   Entry        : {entry:.4f}")
    print(f"[{symbol}]   OTE valid    : {ote_valid}")
    print(f"[{symbol}]   FVG valid    : {fvg_valid}")
    print(f"[{symbol}]   BOS valid    : {bos_ok}")
    print(f"[{symbol}]   COS valid    : {cos_ok}")
    print(f"[{symbol}]   MA200 trend  : {ma200_ok}")
    print(f"[{symbol}]   MACD histo   : {macd_value:.5f}")
    print(f"[{symbol}]   MACRO        : {'✅' if macro_ok else '❌'}")
    print(f"[{symbol}]   ➤ BTC trend     : {'⬆️' if btc_trend else '⬇️'}")
    print(f"[{symbol}]   ➤ TOTAL trend   : {'⬆️' if total_trend else '⬇️'}")
    print(f"[{symbol}]   ➤ BTC.D trend   : {'⬆️' if btc_d_trend else '⬇️'} (non bloquant)")
    print(f"[{symbol}]   CONFIRM 4H   : {'✅' if confirm_ok else '❌'}")
    print(f"[{symbol}]   Bougie valide : {candle_ok}")
    print(f"[{symbol}]   Volume OK     : {volume_ok} (actuel: {df_1h['volume'].iloc[-1]:.2f} / moy: {df_1h['volume'].rolling(20).mean().iloc[-1]:.2f})")
    print(f"[{symbol}]   Score qualité : {score}/10")

    if score < 8:
        print(f"[{symbol}] ❌ Rejeté (score qualité insuffisant)\n")
        return None

    chart_path = generate_chart(df_1h, entry, sl, tp1, tp2, direction)

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
        "tolere_ote": tolere_ote
    }

# Fonctions auxiliaires inchangées sauf macro

def is_macro_valid(btc_df, total_df, btc_d_df, direction):
    btc_trend = btc_df["close"].iloc[-1] > btc_df["close"].iloc[-5]
    total_trend = total_df["close"].iloc[-1] > total_df["close"].iloc[-5]
    btc_d_trend = btc_d_df["close"].iloc[-1] > btc_d_df["close"].iloc[-5]
    
    if direction == "long":
        macro_ok = btc_trend and total_trend
    else:
        macro_ok = not btc_trend and not total_trend

    return macro_ok, btc_trend, total_trend, btc_d_trend

# Les autres fonctions (is_ma200_valid, is_ote, etc.) restent inchangées
