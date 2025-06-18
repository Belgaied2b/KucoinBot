import pandas as pd
import numpy as np
from indicators import compute_macd_histogram, compute_rsi, compute_ma, compute_atr, compute_fvg_zones, find_pivots
from structure_utils import detect_bos_cos, detect_choch
from chart_generator import generate_chart

def analyze_signal(df, direction, btc_df, total_df, btc_d_df):
    try:
        if 'timestamp' not in df.columns:
            print("⚠️ Données invalides : colonne 'timestamp' manquante.")
            return None

        df = df.copy()
        df['timestamp'] = pd.to_datetime(df['timestamp'], unit='ms', errors='coerce')
        df = df.dropna(subset=['timestamp'])
        df.set_index('timestamp', inplace=True)
        df = df.tail(150)

        if len(df) < 50:
            print("⚠️ Pas assez de données pour l’analyse.")
            return None

        close = df['close']
        volume = df['volume']
        ma200 = compute_ma(df)
        atr = compute_atr(df)
        macd_hist = compute_macd_histogram(close)
        fvg_df = compute_fvg_zones(df)

        df['ma200'] = ma200
        df['atr'] = atr
        df['macd_hist'] = macd_hist
        df['fvg_upper'] = fvg_df['fvg_upper']
        df['fvg_lower'] = fvg_df['fvg_lower']

        # ✅ OTE zone
        last_pivot = close.iloc[-20] if len(close) >= 20 else close.mean()
        ote_high = last_pivot * 1.0
        ote_low = last_pivot * 0.786 if direction == "long" else last_pivot * 1.272
        ote_zone = (ote_high, ote_low) if direction == "long" else (ote_low, ote_high)
        in_ote = ote_low <= close.iloc[-1] <= ote_high

        # ✅ FVG zone
        last_fvg = df[['fvg_upper', 'fvg_lower']].dropna().tail(1)
        if not last_fvg.empty:
            fvg_zone = (last_fvg['fvg_upper'].values[0], last_fvg['fvg_lower'].values[0])
            in_fvg = fvg_zone[1] <= close.iloc[-1] <= fvg_zone[0]
        else:
            fvg_zone = (None, None)
            in_fvg = False

        # 🔍 Structure
        bos_ok, cos_ok = detect_bos_cos(df, direction)
        choch_ok = detect_choch(df, direction)

        # ✅ Bougie & volume
        candle_ok = close.iloc[-1] > df['open'].iloc[-1]
        volume_ok = volume.iloc[-1] > volume.rolling(20).mean().iloc[-1] * 1.2
        atr_ok = atr.iloc[-1] > atr.rolling(20).mean().iloc[-1]

        # ✅ Tendance
        ma_trend_ok = close.iloc[-1] > ma200.iloc[-1] if direction == "long" else close.iloc[-1] < ma200.iloc[-1]
        macd_ok = macd_hist.iloc[-1] > 0 if direction == "long" else macd_hist.iloc[-1] < 0

        # ✅ Macro : TOTAL seulement
        total_diff = total_df['close'].iloc[-1] - total_df['close'].iloc[-5]
        macro_ok = (total_diff > 0) if direction == "long" else (total_diff < 0)

        # 🧠 Score et filtres
        rejected = []
        tolerated = []
        score = 0

        if in_ote:
            tolerated.append("OTE")
        else:
            rejected.append("OTE")

        for name, ok in [
            ("FVG", in_fvg),
            ("BOS", bos_ok),
            ("COS", cos_ok),
            ("CHoCH", choch_ok),
            ("MA200", ma_trend_ok),
            ("MACD", macd_ok),
            ("VOLUME", volume_ok),
            ("BOUGIE", candle_ok),
            ("ATR", atr_ok),
            ("CONFIRM 4H", True),  # Placeholder
            ("MACRO", macro_ok)
        ]:
            if name == "OTE":
                continue
            if ok:
                score += 1
            else:
                rejected.append(name)

        if score < 8:
            print(f"❌ Score insuffisant : {score}")
            return None

        # 📌 Entry / SL / TP dynamiques
        entry = close.iloc[-1]
        sl = entry - atr.iloc[-1] if direction == "long" else entry + atr.iloc[-1]
        tp1 = entry + (entry - sl) * 1.0 if direction == "long" else entry - (sl - entry) * 1.0
        tp2 = entry + (entry - sl) * 2.0 if direction == "long" else entry - (sl - entry) * 2.0

        rr1 = round((tp1 - entry) / (entry - sl), 2)
        rr2 = round((tp2 - entry) / (entry - sl), 2)

        # 📊 Graphique
        image_path = generate_chart(df.reset_index(), symbol=df.name, ote_zone=ote_zone, fvg_zone=fvg_zone, entry=entry, sl=sl, tp=tp1, direction=direction.upper())

        # ✅ Résultat final
        return {
            "symbol": df.name,
            "direction": direction.upper(),
            "entry": entry,
            "sl": sl,
            "tp1": tp1,
            "tp2": tp2,
            "rr1": rr1,
            "rr2": rr2,
            "score": score,
            "tolere_ote": not in_ote,
            "toleres": tolerated,
            "rejetes": rejected,
            "comment": f"📌 Zone idéale d'entrée :\nOTE = {ote_zone[1]:.4f} → {ote_zone[0]:.4f}\nFVG = {fvg_zone[1]:.4f} → {fvg_zone[0]:.4f}" if fvg_zone[0] else ""
        }

    except Exception as e:
        print(f"⚠️ Erreur analyse signal : {e}")
        return None
