import numpy as np
from indicators import (
    compute_rsi,
    compute_macd_histogram,
    compute_ma,
    compute_atr,
    compute_fvg_zones
)
from structure_utils import detect_bos_cos, detect_choch
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

        # 🟦 OTE (Optimal Trade Entry)
        high_price = df['high'].rolling(window=50).max().iloc[-1]
        low_price = df['low'].rolling(window=50).min().iloc[-1]
        if direction == "long":
            ote_start = low_price + 0.618 * (high_price - low_price)
            ote_end = low_price + 0.786 * (high_price - low_price)
            in_ote = ote_start <= last_close <= ote_end
        else:
            ote_start = high_price - 0.786 * (high_price - low_price)
            ote_end = high_price - 0.618 * (high_price - low_price)
            in_ote = ote_start >= last_close >= ote_end

        # 🟧 FVG
        fvg_df = compute_fvg_zones(df)
        fvg_upper = fvg_df['fvg_upper'].iloc[-1]
        fvg_lower = fvg_df['fvg_lower'].iloc[-1]
        in_fvg = False
        if not np.isnan(fvg_upper) and not np.isnan(fvg_lower):
            in_fvg = fvg_lower <= last_close <= fvg_upper

        # 🔁 Volume
        avg_volume = df['volume'].rolling(window=20).mean().iloc[-1]
        last_volume = df['volume'].iloc[-1]
        volume_ok = last_volume > avg_volume * 1.2

        # 📈 MA200
        ma200 = compute_ma(df)
        ma_ok = last_close > ma200.iloc[-1] if direction == "long" else last_close < ma200.iloc[-1]

        # 🟢 MACD Histogramme
        macd_hist = compute_macd_histogram(df['close'])
        macd_ok = macd_hist.iloc[-1] > 0 if direction == "long" else macd_hist.iloc[-1] < 0

        # 🔁 BOS / COS
        bos_ok, cos_ok = detect_bos_cos(df, direction)

        # 🔄 CHoCH
        choch_ok = detect_choch(df, direction)

        # 🔥 Bougie forte
        close = df['close'].iloc[-1]
        open_ = df['open'].iloc[-1]
        volume = df['volume'].iloc[-1]
        body = abs(close - open_)
        wick = df['high'].iloc[-1] - df['low'].iloc[-1]
        body_ratio = body / wick if wick > 0 else 0
        candle_ok = body_ratio > 0.5 and volume > avg_volume

        # 📉 Volatilité via ATR
        atr = compute_atr(df)
        atr_value = atr.iloc[-1]
        atr_ok = atr_value > 0.005 * last_close

        # 📊 Contexte macro : TOTAL & BTC
        total_slope = total_df['close'].diff().rolling(window=5).mean().iloc[-1]
        btc_slope = btc_df['close'].diff().rolling(window=5).mean().iloc[-1]
        market_ok = total_slope > 0 if direction == "long" else total_slope < 0
        btc_ok = btc_slope > 0 if direction == "long" else btc_slope < 0

        # 📊 BTC.D
        btc_d_change = btc_d_df['close'].diff().rolling(window=5).mean().iloc[-1]
        btc_d_status = "HAUSSIER" if btc_d_change > 0 else "BAISSIER" if btc_d_change < 0 else "STAGNANT"

        # 🎯 SL / TP dynamiques
        sl = last_close + atr_value * 2 if direction == "short" else last_close - atr_value * 2
        tp1 = last_close - atr_value * 2 if direction == "short" else last_close + atr_value * 2
        tp2 = last_close - atr_value * 4 if direction == "short" else last_close + atr_value * 4
        rr1 = round(abs(tp1 - last_close) / abs(sl - last_close), 1)
        rr2 = round(abs(tp2 - last_close) / abs(sl - last_close), 1)

        # 📋 Validation stricte
        rejected = []
        tolerated = []

        if not volume_ok: rejected.append("VOLUME")
        if not ma_ok: rejected.append("MA200")
        if not macd_ok: rejected.append("MACD")
        if not bos_ok: rejected.append("BOS")
        if not cos_ok: rejected.append("COS")
        if not choch_ok: rejected.append("CHoCH")
        if not candle_ok: tolerated.append("BOUGIE")
        if not atr_ok: rejected.append("ATR")
        if not in_fvg: rejected.append("FVG")
        if not in_ote: tolerated.append("OTE")
        if not market_ok: rejected.append("TOTAL")
        if not btc_ok: rejected.append("BTC")

        # 🧠 Score pondéré expert
        score = 10
        score -= 0.4 * len(tolerated)
        if not in_ote:
            score -= 0.2
        score = round(max(score, 0), 2)  # Jamais négatif

        # 💬 Commentaire complet
        comment = (
            f"📌 Zone idéale d'entrée :\n"
            f"OTE = {round(ote_start, 4)} → {round(ote_end, 4)}\n"
            f"FVG = {round(fvg_lower, 4)} → {round(fvg_upper, 4)}\n\n"
            f"📊 BTC Dominance : {btc_d_status}"
        )

        if rejected:
            return {
                "valid": False,
                "score": score,
                "rejetes": rejected,
                "toleres": tolerated,
                "comment": comment
            }

        # ✅ Génération du graphique
        generate_chart(
            df,
            symbol,
            ote_zone=(ote_start, ote_end),
            fvg_zone=(fvg_lower, fvg_upper),
            entry=last_close,
            sl=sl,
            tp=tp1,
            direction=direction
        )

        return {
            "valid": True,
            "symbol": symbol,
            "direction": direction.upper(),
            "entry": round(last_close, 4),
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
        return {
            "valid": False,
            "score": 0,
            "rejetes": ["erreur interne"],
            "toleres": [],
            "comment": str(e)
        }
