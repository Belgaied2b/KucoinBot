import pandas as pd
from indicators import (
    is_ema_trend_ok, is_momentum_ok, is_bos_with_strength,
    is_cos_enhanced, is_bullish_engulfing, is_bearish_engulfing,
    is_bullish_divergence, is_bearish_divergence,
    is_volume_strong, is_btc_ok, is_total_ok,
    is_btc_at_key_level, is_liquidity_zone_present, is_aggressive_volume_ok,
    is_atr_sufficient
)
from structure_utils import (
    is_bos_valid, is_cos_valid, is_choch, find_structure_tp
)
from institutional_live import live_data

# 🧠 Analyse complète d’un signal
def analyze_signal(symbol, df_h1, df_h4, df_btc, df_total, df_total2, df_dominance, direction="long"):
    if df_h1 is None or len(df_h1) < 100:
        return None

    score = 0
    tolerances = []
    comments = []

    # ⚠️ Intégration institutionnelle en priorité
    symbol_binance = symbol.replace("USDTM", "").lower()
    inst_data = live_data.get(symbol_binance, {})
    inst_score = inst_data.get("last_score", 0)
    inst_details = inst_data.get("last_details", [])
    if inst_score >= 2:
        score += 2
        comments.append(f"💼 INSTITUTIONNEL OK ({inst_score}/4: {', '.join(inst_details)})")
    else:
        comments.append("❌ Score institutionnel insuffisant")

    # ✅ Analyse technique seulement si institutionnel > 1
    if inst_score >= 2:
        if is_ema_trend_ok(df_h1, direction):
            score += 1
            comments.append("✅ EMA20/EMA50 OK")
        else:
            tolerances.append("EMA")

        if is_momentum_ok(df_h1, direction):
            score += 1
            comments.append("✅ Momentum MACD/RSI OK")
        else:
            tolerances.append("MOMENTUM")

        if is_bos_with_strength(df_h1, direction):
            score += 1
            comments.append("✅ BOS avec volume OK")
        else:
            tolerances.append("BOS")

        if is_cos_enhanced(df_h1, direction):
            score += 1
            comments.append("✅ COS + divergence/volume OK")

        if is_bullish_engulfing(df_h1) if direction == "long" else is_bearish_engulfing(df_h1):
            score += 1
            comments.append("✅ Bougie engulfing")

        if is_bullish_divergence(df_h1) if direction == "long" else is_bearish_divergence(df_h1):
            score += 1
            comments.append("✅ Divergence RSI")

        if is_volume_strong(df_h1):
            score += 1
            comments.append("✅ Volume fort")

        if is_liquidity_zone_present(df_h1, direction):
            tolerances.append("LIQUIDITE")
            comments.append("ℹ️ Zone de liquidité détectée")

        if is_aggressive_volume_ok(df_h1, direction):
            score += 1
            comments.append("✅ Volume agressif OK")

        if not is_atr_sufficient(df_h1):
            tolerances.append("ATR")
            comments.append("⚠️ ATR insuffisant")

        if not is_btc_ok(df_btc):
            tolerances.append("BTC")
            comments.append("⚠️ BTC pas aligné")

        if not is_total_ok(df_total, direction) and not is_total_ok(df_total2, direction):
            tolerances.append("TOTAL")
            comments.append("⚠️ TOTAL pas aligné")

        if is_btc_at_key_level(df_btc):
            comments.append("🔑 BTC sur niveau clé")

        if is_bos_valid(df_h1, direction):
            comments.append("📈 BOS détecté")
        if is_cos_valid(df_h1, direction):
            comments.append("📉 COS détecté")
        if is_choch(df_h1, direction):
            comments.append("🔄 CHoCH détecté")

    # 🧮 Résultat final
    valid = score >= 4
    return {
        "symbol": symbol,
        "direction": direction,
        "score": score,
        "valid": valid,
        "tolerances": tolerances,
        "comments": comments,
        "entry": df_h1["close"].iloc[-1],
        "sl": df_h1["low"].iloc[-3] if direction == "long" else df_h1["high"].iloc[-3],
        "tp": find_structure_tp(df_h1, direction)
    }
