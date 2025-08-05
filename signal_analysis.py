import numpy as np
from indicators import (
    compute_atr, compute_fvg_zones, is_volume_strong,
    is_above_ma200, is_below_ma200,
    is_macd_positive, is_macd_negative,
    is_bullish_divergence, is_bearish_divergence,
    is_atr_sufficient, is_total_ok, is_btc_ok,
    get_btc_dominance_trend
)
from structure_utils import (
    is_bos_valid, is_cos_valid, is_choch,
    is_bullish_engulfing, is_bearish_engulfing,
    find_structure_tp
)
from chart_generator import generate_chart


def analyze_signal(df, symbol, direction, btc_df, total_df, btc_d_df):
    if df is None or df.empty or 'timestamp' not in df.columns:
        return {
            "valid": False,
            "score": 0,
            "rejetes": ["données invalides"],
            "toleres": [],
            "comment": "DataFrame vide ou colonne 'timestamp' manquante"
        }

    try:
        df = df.copy()
        df.sort_index(inplace=True)
        last_close = df['close'].iloc[-1]

        high_price = df['high'].rolling(window=50).max().iloc[-1]
        low_price = df['low'].rolling(window=50).min().iloc[-1]

        if direction == "long":
            ote_start = low_price + 0.618 * (high_price - low_price)
            ote_end = low_price + 0.786 * (high_price - low_price)
            in_ote = ote_start <= last_close <= ote_end
            entry_price = ote_end
        else:
            ote_start = high_price - 0.786 * (high_price - low_price)
            ote_end = high_price - 0.618 * (high_price - low_price)
            in_ote = ote_start >= last_close >= ote_end
            entry_price = ote_end

        fvg_df = compute_fvg_zones(df)
        fvg_upper = fvg_df['fvg_upper'].iloc[-1]
        fvg_lower = fvg_df['fvg_lower'].iloc[-1]
        in_fvg = False
        if not np.isnan(fvg_upper) and not np.isnan(fvg_lower):
            if direction == "long":
                in_fvg = fvg_lower <= last_close <= fvg_upper
            else:
                in_fvg = fvg_upper >= last_close >= fvg_lower

        # 🔍 Indicateurs
        volume_ok = is_volume_strong(df)
        ma_ok = is_above_ma200(df) if direction == "long" else is_below_ma200(df)
        macd_ok = is_macd_positive(df) if direction == "long" else is_macd_negative(df)
        bos_ok = is_bos_valid(df, direction)
        cos_ok = is_cos_valid(df, direction)
        choch_ok = is_choch(df, direction)
        candle_ok = is_bullish_engulfing(df) if direction == "long" else is_bearish_engulfing(df)
        divergence_ok = is_bullish_divergence(df) if direction == "long" else is_bearish_divergence(df)
        atr_ok = is_atr_sufficient(df)
        market_ok = is_total_ok(total_df, direction)
        btc_ok = is_btc_ok(btc_df)
        btc_d_status = get_btc_dominance_trend(btc_d_df)

        # 🛑 SL / TP dynamiques
        atr = compute_atr(df)
        atr_value = atr.iloc[-1]
        if direction == "long":
            sl = min(df['low'].iloc[-10:]) - atr_value * 0.5
            tp1 = find_structure_tp(df, direction, entry_price)
        else:
            sl = max(df['high'].iloc[-10:]) + atr_value * 0.5
            tp1 = find_structure_tp(df, direction, entry_price)

        tp2 = entry_price + (tp1 - entry_price) * 2 if direction == "long" else entry_price - (entry_price - tp1) * 2
        rr1 = round(abs(tp1 - entry_price) / abs(entry_price - sl), 1)
        rr2 = round(abs(tp2 - entry_price) / abs(entry_price - sl), 1)

        # ⚖️ Tolérances
        tolerable = {"OTE", "BOUGIE", "DIVERGENCE", "RR"}
        tolerated = []
        rejected = []

        if not volume_ok: rejected.append("VOLUME")
        if not ma_ok: rejected.append("MA200")
        if not macd_ok: rejected.append("MACD")
        if not bos_ok: rejected.append("BOS")
        if not atr_ok: rejected.append("ATR")
        if not market_ok: rejected.append("TOTAL")
        if not btc_ok: rejected.append("BTC")

        if not cos_ok: tolerated.append("COS")
        if not choch_ok: tolerated.append("CHoCH")
        if not candle_ok: tolerated.append("BOUGIE")
        if not in_fvg: tolerated.append("FVG")
        if not in_ote: tolerated.append("OTE")
        if not divergence_ok: tolerated.append("DIVERGENCE")

        # 🎯 R:R minimum
        if rr1 < 1.5:
            tolerated.append("RR")

        tolerated = [t for t in tolerated if t in tolerable]
        rejected += [t for t in tolerated if t not in tolerable]

        # 📊 Score
        poids = {
            "MA200": 1.5, "MACD": 1.5, "BOS": 1.5,
            "COS": 1.0, "CHoCH": 1.0, "FVG": 1.0,
            "VOLUME": 1.5, "BOUGIE": 0.5, "TOTAL": 1.0,
            "BTC": 1.0, "DIVERGENCE": 0.5
        }
        score_total = sum(poids.values())
        score_obtenu = sum(v for k, v in poids.items() if k not in rejected)
        score = round((score_obtenu / score_total) * 10, 1)

        # 📝 Commentaire
        comment = (
            f"📌 Zone idéale d'entrée :\n"
            f"OTE = {round(ote_start, 4)} → {round(ote_end, 4)}\n"
            f"FVG = {round(fvg_lower, 4)} → {round(fvg_upper, 4)}\n\n"
            f"📊 BTC Dominance : {btc_d_status}\n"
            f"📈 Score : {score}/10\n"
            f"❌ Rejetés : {', '.join(rejected) if rejected else 'aucun'}\n"
            f"⚠️ Tolérés : {', '.join(tolerated) if tolerated else 'aucun'}\n\n"
            f"ℹ️ Seuls OTE, BOUGIE, DIVERGENCE peuvent être tolérés"
        )

        if rejected:
            return {
                "valid": False,
                "score": score,
                "rejetes": rejected,
                "toleres": tolerated,
                "comment": comment
            }

        # 📈 Graphique
        generate_chart(
            df, symbol,
            ote_zone=(ote_start, ote_end),
            fvg_zone=(fvg_lower, fvg_upper),
            entry=entry_price,
            sl=sl,
            tp=tp1,
            direction=direction
        )

        return {
            "valid": True,
            "symbol": symbol,
            "direction": direction.upper(),
            "entry": round(entry_price, 4),
            "sl": round(sl, 4),
            "tp1": round(tp1, 4),
            "tp2": round(tp2, 4),
            "rr1": rr1,
            "rr2": rr2,
            "score": score,
            "toleres": tolerated,
            "rejetes": rejected,
            "comment": comment,
            "tolere_ote": "OTE" in tolerated
        }

    except Exception as e:
        import traceback
        print("Erreur dans analyze_signal:\n", traceback.format_exc())
        return {
            "valid": False,
            "score": 0,
            "rejetes": ["erreur"],
            "toleres": [],
            "comment": f"{type(e).__name__} : {str(e)}"
        }
