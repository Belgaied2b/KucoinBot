import numpy as np
from indicators import (
    compute_atr, compute_fvg_zones,
    is_momentum_ok, is_ema_trend_ok,
    is_bos_with_strength, is_cos_enhanced,
    is_bullish_divergence, is_bearish_divergence,
    is_atr_sufficient, is_total_ok, is_btc_ok,
    is_btc_at_key_level, get_btc_dominance_trend,
    is_aggressive_volume_ok, has_liquidity_zone
)
from institutional_data import get_institutional_score
from structure_utils import (
    is_choch,
    is_bullish_engulfing, is_bearish_engulfing,
    find_structure_tp
)
from chart_generator import generate_chart

def analyze_signal(df, symbol, direction, btc_df, total_df, btc_d_df, total2_df=None, df_higher_tf=None):
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

        # Analyse technique
        momentum_ok = is_momentum_ok(df, direction)
        ema_trend_ok = is_ema_trend_ok(df, direction)
        bos_ok = is_bos_with_strength(df, direction)
        cos_ok = is_cos_enhanced(df, direction)
        choch_ok = is_choch(df, direction)
        candle_ok = is_bullish_engulfing(df) if direction == "long" else is_bearish_engulfing(df)
        divergence_ok = is_bullish_divergence(df) if direction == "long" else is_bearish_divergence(df)
        atr_ok = is_atr_sufficient(df)
        volume_aggressif_ok = is_aggressive_volume_ok(df, direction)
        liquidity_zone_ok = has_liquidity_zone(df, direction)

        # Analyse H4
        ema_trend_h4 = is_ema_trend_ok(df_higher_tf, direction) if df_higher_tf is not None else True
        momentum_h4 = is_momentum_ok(df_higher_tf, direction) if df_higher_tf is not None else True

        # Macro
        market_ok = is_total_ok(total_df, direction)
        total2_ok = is_total_ok(total2_df, direction) if total2_df is not None else True
        btc_ok = is_btc_ok(btc_df)
        btc_level_ok = is_btc_at_key_level(btc_df)
        btc_d_status = get_btc_dominance_trend(btc_d_df)

        # Institutionnel
        symbol_binance = symbol.replace("USDTM", "USDT")
        institutional_score, institutional_details = get_institutional_score(df, symbol_binance)
        institutional_ok = institutional_score >= 2

        # SL & TP
        atr = compute_atr(df)
        atr_value = atr.iloc[-1]
        if direction == "long":
            sl = (df['low'].min() - atr_value * 0.25) if liquidity_zone_ok else min(df['low'].iloc[-10:]) - atr_value * 0.5
        else:
            sl = (df['high'].max() + atr_value * 0.25) if liquidity_zone_ok else max(df['high'].iloc[-10:]) + atr_value * 0.5

        tp1 = find_structure_tp(df, direction, entry_price)
        if tp1 is None or np.isnan(tp1):
            tp1 = entry_price + (entry_price - sl) * 1.5 if direction == "long" else entry_price - (sl - entry_price) * 1.5

        tp2 = entry_price + (tp1 - entry_price) * 2 if direction == "long" else entry_price - (entry_price - tp1) * 2
        rr1 = round(abs(tp1 - entry_price) / abs(entry_price - sl), 1)
        rr2 = round(abs(tp2 - entry_price) / abs(entry_price - sl), 1)

        # Vérification institutionnelle renforcée
        nb_tech_ok = sum([
            ema_trend_ok, momentum_ok, bos_ok, cos_ok,
            divergence_ok, candle_ok, choch_ok
        ])

        if institutional_ok and rr1 >= 1.5 and liquidity_zone_ok and atr_ok and nb_tech_ok >= 4:
            generate_chart(df, symbol, ote_zone=(ote_start, ote_end), fvg_zone=(fvg_lower, fvg_upper), entry=entry_price, sl=sl, tp=tp1, direction=direction)
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
                "score": 10.0,
                "toleres": [],
                "rejetes": [],
                "comment": f"✅ Signal institutionnel : confluence validée\n🏦 Institutionnel : {' / '.join(institutional_details)}\n📈 RR: {rr1}, ATR OK, Zone de liquidité détectée\n🎯 Nb indicateurs techniques validés : {nb_tech_ok}/7",
                "tolere_ote": False,
                "ote_zone": (round(ote_start, 4), round(ote_end, 4)),
                "fvg_zone": (round(fvg_lower, 4), round(fvg_upper, 4)),
                "btc_dominance": btc_d_status
            }

        return {
            "valid": False,
            "score": 0,
            "rejetes": ["Conditions institutionnelles non remplies"],
            "toleres": [],
            "comment": (
                f"❌ Signal institutionnel rejeté\n"
                f"🏦 Institutionnel : {' / '.join(institutional_details) if institutional_details else 'aucun'}\n"
                f"📉 RR: {rr1}, ATR OK : {atr_ok}, Liquidité : {liquidity_zone_ok}\n"
                f"🎯 Nb indicateurs techniques validés : {nb_tech_ok}/7"
            )
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
