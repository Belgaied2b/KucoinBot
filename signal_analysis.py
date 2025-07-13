import pandas as pd
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

    # 🔐 Force la conversion en float pour éviter les erreurs de type
    for col in ["open", "close", "high", "low", "volume"]:
        df[col] = pd.to_numeric(df[col], errors="coerce")
    df.dropna(subset=["open", "close", "high", "low", "volume"], inplace=True)
    if len(df) < 30:
        result["comment"] = "Pas assez de données après nettoyage."
        return result

    df["rsi"] = compute_rsi(df["close"])
    df["macd_histogram"] = compute_macd_histogram(df["close"])
    df["ma200"] = compute_ma(df)
    df["atr"] = compute_atr(df)

    # Structure
    try:
        bos, cos, choch = detect_bos_cos_choch(df, direction)
    except Exception as e:
        result["comment"] = f"Erreur structure (BOS/COS/CHoCH) : {e}"
        return result

    if not bos:
        result["rejetes"].append("BOS")
    if not cos:
        result["rejetes"].append("COS")
    if not choch:
        result["rejetes"].append("CHoCH")

    # Divergences
    divergence_valid = detect_divergence(df, direction)
    if not divergence_valid:
        result["toleres"].append("DIVERGENCE")

    # Zones OTE + FVG
    ote_zone = calculate_ote_zone(df, direction)
    fvg_zones = compute_fvg_zones(df)
    entry = find_entry_in_ote_fvg(df, ote_zone, fvg_zones, direction)
    if entry is None:
        result["toleres"].append("OTE")

    # Bougie de confirmation
    latest_close = df["close"].iloc[-1]
    latest_open = df["open"].iloc[-1]
    latest_volume = df["volume"].iloc[-1]
    avg_volume = df["volume"].mean()

    candle_valid = (
        (latest_close > latest_open if direction == "long" else latest_close < latest_open)
        and latest_volume > avg_volume
    )
    if not candle_valid:
        result["toleres"].append("BOUGIE")

    # Volume
    if latest_volume < avg_volume * 1.2:
        result["rejetes"].append("VOLUME")

    # MACD
    macd_valid = df["macd_histogram"].iloc[-1] > 0 if direction == "long" else df["macd_histogram"].iloc[-1] < 0
    if not macd_valid:
        result["rejetes"].append("MACD")

    # MA200
    price = df["close"].iloc[-1]
    ma200_valid = price > df["ma200"].iloc[-1] if direction == "long" else price < df["ma200"].iloc[-1]
    if not ma200_valid:
        result["rejetes"].append("MA200")

    # Macro contexte
    total_ok, btc_d_trend, total_trend = check_market_conditions(direction, btc_df, total_df, btcd_df)
    if not total_ok:
        result["rejetes"].append("MACRO TOTAL")

    # SL/TP dynamiques
    sl = price - 1.5 * df["atr"].iloc[-1] if direction == "long" else price + 1.5 * df["atr"].iloc[-1]
    tp = find_dynamic_tp(df, price, sl, direction)
    if tp is None:
        result["rejetes"].append("TP")

    # Calcul du score
    score = 10
    for rej in result["rejetes"]:
        if rej in ["VOLUME", "MACRO TOTAL", "MACD", "MA200", "BOS", "COS", "CHoCH"]:
            score -= 2
        else:
            score -= 1
    for tol in result["toleres"]:
        score -= 0.5
    result["score"] = max(score, 0)

    if result["score"] < 8 or len(result["rejetes"]) > 0:
        result["comment"] = f"Signal rejeté – Score : {result['score']}/10 ❌ Rejetés : {', '.join(result['rejetes'])} ⚠️ Tolérés : {', '.join(result['toleres'])}"
        return result

    # Génération du graphique
    chart_path = generate_chart(df, symbol, ote_zone, fvg_zones, entry, sl, tp, direction)

    # Résultat final
    result.update({
        "is_valid": True,
        "entry": round(entry if entry else price, 4),
        "sl": round(sl, 4),
        "tp": round(tp, 4),
        "chart_path": chart_path,
        "comment": f"✅ Signal confirmé – Score : {result['score']}/10 ⚠️ Tolérés : {', '.join(result['toleres'])}" +
                   (f" (BTC.D : {btc_d_trend}, TOTAL : {total_trend})" if btc_d_trend else "")
    })

    return result
