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

        # OTE
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

        # FVG
        fvg_df = compute_fvg_zones(df)
        fvg_upper = fvg_df['fvg_upper'].iloc[-1]
        fvg_lower = fvg_df['fvg_lower'].iloc[-1]
        in_fvg = False
        if not np.isnan(fvg_upper) and not np.isnan(fvg_lower):
            if direction == "long" and fvg_lower < fvg_upper:
                in_fvg = fvg_lower <= last_close <= fvg_upper
            elif direction == "short" and fvg_upper < fvg_lower:
                in_fvg = fvg_upper <= last_close <= fvg_lower

        # Volume
        avg_volume = df['volume'].rolling(window=20).mean().iloc[-1]
        last_volume = df['volume'].iloc[-1]
        volume_ok = last_volume > avg_volume * 1.2

        # MA200
        ma200 = compute_ma(df)
        ma_ok = last_close > ma200.iloc[-1] if direction == "long" else last_close < ma200.iloc[-1]

        # MACD
        macd_hist = compute_macd_histogram(df['close'])
        macd_ok = macd_hist.iloc[-1] > 0 if direction == "long" else macd_hist.iloc[-1] < 0

        # Divergences
        rsi = compute_rsi(df['close'])
        rsi_div = rsi.iloc[-3] > rsi.iloc[-2] < rsi.iloc[-1] if direction == "long" else rsi.iloc[-3] < rsi.iloc[-2] > rsi.iloc[-1]
        macd_div = macd_hist.iloc[-3] > macd_hist.iloc[-2] < macd_hist.iloc[-1] if direction == "long" else macd_hist.iloc[-3] < macd_hist.iloc[-2] > macd_hist.iloc[-1]
        divergence_ok = rsi_div and macd_div

        # BOS / COS
        bos_ok, cos = detect_bos_cos(df, direction)
        candle = df.iloc[-1]
        breakout_confirm = (candle['close'] > candle['open'] and candle['volume'] > avg_volume) if direction == "long" else (candle['close'] < candle['open'] and candle['volume'] > avg_volume)
        bos_ok = bos_ok and breakout_confirm
        cos_ok = cos and breakout_confirm

        # CHoCH
        choch_ok = detect_choch(df, direction)

        # Bougie forte
        body = abs(df['close'].iloc[-1] - df['open'].iloc[-1])
        wick = df['high'].iloc[-1] - df['low'].iloc[-1]
        body_ratio = body / wick if wick > 0 else 0
        candle_ok = body_ratio > 0.5 and last_volume > avg_volume

        # ATR
        atr = compute_atr(df)
        atr_value = atr.iloc[-1]
        atr_ok = atr_value > 0.005 * last_close

        # Macro TOTAL / BTC
        total_slope = total_df['close'].diff().rolling(window=5).mean().iloc[-1]
        btc_slope = btc_df['close'].diff().rolling(window=5).mean().iloc[-1]
        market_ok = total_slope > 0 if direction == "long" else total_slope < 0
        btc_ok = btc_slope > 0 if direction == "long" else btc_slope < 0

        # BTC.D
        btc_d_change = btc_d_df['close'].diff().rolling(window=5).mean().iloc[-1]
        btc_d_status = "HAUSSIER" if btc_d_change > 0 else "BAISSIER" if btc_d_change < 0 else "STAGNANT"

        # SL / TP dynamiques via structure
        if direction == "long":
            sl = min(df['low'].iloc[-10:]) - atr_value * 0.5
            tp1 = last_close + abs(last_close - sl) * 1.5
            tp2 = last_close + abs(last_close - sl) * 3
        else:
            sl = max(df['high'].iloc[-10:]) + atr_value * 0.5
            tp1 = last_close - abs(last_close - sl) * 1.5
            tp2 = last_close - abs(last_close - sl) * 3

        rr1 = round(abs(tp1 - last_close) / abs(sl - last_close), 1)
        rr2 = round(abs(tp2 - last_close) / abs(sl - last_close), 1)

        # Validation stricte + tolérance intelligente
        rejected = []
        tolerated = []

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

        # Score pondéré
        poids = {
            "MA200": 1.5,
            "MACD": 1.5,
            "BOS": 1.5,
            "COS": 1.0,
            "CHoCH": 1.0,
            "FVG": 1.0,
            "VOLUME": 1.5,
            "BOUGIE": 0.5,
            "TOTAL": 1.0,
            "BTC": 1.0,
            "DIVERGENCE": 0.5
        }

        score_total = sum(poids.values())
        score_obtenu = sum(v for k, v in poids.items() if k not in rejected)
        score = round((score_obtenu / score_total) * 10, 1)

        # Commentaire
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

        # ✅ Génération graphique
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
