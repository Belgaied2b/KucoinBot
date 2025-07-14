import pandas as pd
import numpy as np
from indicators import (
    compute_rsi, compute_macd_histogram, compute_fvg_zones,
    compute_ma, compute_atr, detect_divergence
)
from structure_utils import detect_bos_cos_choch
from chart_generator import generate_chart
from utils import calculate_ote_zone, find_entry_in_ote_fvg, find_dynamic_tp
from macros import check_market_conditions


def analyze_signal(df, symbol, direction, df_4h=None, btc_df=None, total_df=None, btcd_df=None):
    result = {
        "is_valid": False,
        "score": 0,
        "rejetes": [],
        "toleres": [],
        "comment": "",
        "chart_path": None,
        "entry": None,
        "sl": None,
        "tp": None,
        "direction": direction.upper(),
        "symbol": symbol
    }

    if df is None or df.empty or "timestamp" not in df.columns:
        result["comment"] = "Données invalides ou incomplètes."
        return result

    df = df.copy()

    # 🔒 Conversion stricte des colonnes
    float_cols = ["open", "high", "low", "close", "volume"]
    for col in float_cols:
        df[col] = pd.to_numeric(df[col], errors="coerce")
    df.dropna(subset=float_cols, inplace=True)

    # 🔍 Vérification stricte des types
    for col in float_cols:
        if not np.issubdtype(df[col].dtype, np.floating):
            result["comment"] = f"Colonne {col} invalide (type {df[col].dtype})"
            return result

    if len(df) < 30:
        result["comment"] = "Pas assez de données après nettoyage."
        return result

    try:
        df["rsi"] = pd.to_numeric(compute_rsi(df["close"]), errors="coerce")
        df["macd_histogram"] = pd.to_numeric(compute_macd_histogram(df["close"]), errors="coerce")
        df["ma200"] = pd.to_numeric(compute_ma(df), errors="coerce")
        df["atr"] = pd.to_numeric(compute_atr(df), errors="coerce")
        df.dropna(subset=["rsi", "macd_histogram", "ma200", "atr"], inplace=True)
    except Exception as e:
        result["comment"] = f"Erreur calcul indicateurs : {e}"
        return result

    # Structure
    try:
        bos, cos, choch = detect_bos_cos_choch(df, direction)
        if not bos: result["rejetes"].append("BOS")
        if not cos: result["rejetes"].append("COS")
        if not choch: result["rejetes"].append("CHoCH")
    except Exception as e:
        result["comment"] = f"Erreur structure : {e}"
        return result

    # Divergence
    try:
        if not detect_divergence(df, direction):
            result["toleres"].append("DIVERGENCE")
    except:
        result["toleres"].append("DIVERGENCE")

    # OTE / FVG
    try:
        ote_zone = calculate_ote_zone(df, direction)
        fvg_zones = compute_fvg_zones(df)
        entry = find_entry_in_ote_fvg(df, ote_zone, fvg_zones, direction)
        if entry is None:
            result["toleres"].append("OTE")
    except Exception as e:
        result["comment"] = f"Erreur OTE/FVG : {e}"
        return result

    # Bougie de confirmation
    try:
        latest_close = float(df["close"].iloc[-1])
        latest_open = float(df["open"].iloc[-1])
        latest_volume = float(df["volume"].iloc[-1])
        avg_volume = float(df["volume"].mean())
        candle_valid = (
            (latest_close > latest_open if direction == "long" else latest_close < latest_open)
            and latest_volume > avg_volume
        )
        if not candle_valid:
            result["toleres"].append("BOUGIE")
    except Exception as e:
        result["comment"] = f"Erreur bougie : {e}"
        return result

    # Volume
    try:
        if latest_volume < avg_volume * 1.2:
            result["rejetes"].append("VOLUME")
    except Exception as e:
        result["comment"] = f"Erreur volume : {e}"
        return result

    # MACD
    try:
        macd_value = float(df["macd_histogram"].iloc[-1])
        macd_ok = macd_value > 0 if direction == "long" else macd_value < 0
        if not macd_ok:
            result["rejetes"].append("MACD")
    except Exception as e:
        result["comment"] = f"Erreur MACD : {e}"
        return result

    # MA200
    try:
        ma200 = float(df["ma200"].iloc[-1])
        price = float(df["close"].iloc[-1])
        ma_ok = price > ma200 if direction == "long" else price < ma200
        if not ma_ok:
            result["rejetes"].append("MA200")
    except Exception as e:
        result["comment"] = f"Erreur MA200 : {e}"
        return result

    # Macro
    try:
        total_ok, btc_d_trend, total_trend = check_market_conditions(direction, btc_df, total_df, btcd_df)
        if not total_ok:
            result["rejetes"].append("MACRO TOTAL")
    except Exception as e:
        result["comment"] = f"Erreur macro : {e}"
        return result

    # SL / TP
    try:
        atr = float(df["atr"].iloc[-1])
        sl = price - 1.5 * atr if direction == "long" else price + 1.5 * atr
        tp = find_dynamic_tp(df, price, sl, direction)
        if tp is None:
            result["rejetes"].append("TP")
    except Exception as e:
        result["comment"] = f"Erreur SL/TP : {e}"
        return result

    # Score pondéré
    score = 10
    for rej in result["rejetes"]:
        score -= 2 if rej in ["VOLUME", "MACRO TOTAL", "MACD", "MA200", "BOS", "COS", "CHoCH"] else 1
    for tol in result["toleres"]:
        score -= 0.5
    result["score"] = max(0, score)

    if result["score"] < 8 or result["rejetes"]:
        result["comment"] = (
            f"Signal rejeté – Score : {result['score']}/10 "
            f"❌ Rejetés : {', '.join(result['rejetes'])} "
            f"⚠️ Tolérés : {', '.join(result['toleres'])}"
        )
        return result

    # Graphique
    try:
        chart_path = generate_chart(df, symbol, ote_zone, fvg_zones, entry, sl, tp, direction)
    except:
        chart_path = None

    # Résultat final
    result.update({
        "is_valid": True,
        "entry": round(entry if entry else price, 4),
        "sl": round(sl, 4),
        "tp": round(tp, 4),
        "chart_path": chart_path,
        "comment": (
            f"✅ Signal confirmé – Score : {result['score']}/10 "
            f"⚠️ Tolérés : {', '.join(result['toleres'])}"
            + (f" (BTC.D : {btc_d_trend}, TOTAL : {total_trend})" if btc_d_trend else "")
        )
    })

    return result
