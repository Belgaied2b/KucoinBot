"""
signal_engine.py — moteur de détection pro (confluence)
"""
import math
import numpy as np
import pandas as pd
from typing import Tuple, Optional, Dict, Any

from advanced_indicators import adx, squeeze_on, obv, ema_cloud, atr as ind_atr, hv_percentile
from institutional_data import compute_full_institutional_analysis
from liquidity_map import fetch_liquidations_heatmap
from microstructure import fetch_orderbook_snapshot

def _last(series: pd.Series, default=np.nan) -> float:
    try:
        return float(series.iloc[-1])
    except Exception:
        return float(default)

def _build_rr_safe(bias: str, entry: float, atr_val: float, k_sl=1.5, k_tp=2.0) -> Tuple[Optional[dict], Optional[str]]:
    if not np.isfinite(atr_val) or atr_val <= 0:
        atr_val = max(entry * 0.003, 1e-6)
    if bias == "LONG":
        sl = entry - k_sl * atr_val
        tp = entry + k_tp * atr_val
        stop = entry - sl; prof = tp - entry
    else:
        sl = entry + k_sl * atr_val
        tp = entry - k_tp * atr_val
        stop = sl - entry; prof = entry - tp
    if stop <= 0 or prof <= 0 or not np.isfinite(stop) or not np.isfinite(prof):
        return None, "Distances SL/TP invalides"
    rr = prof / stop
    if not np.isfinite(rr) or rr <= 0:
        return None, "RR invalide"
    return {"sl": sl, "tp1": entry + (prof/2 if bias=="LONG" else -prof/2), "tp2": tp, "rr": float(rr)}, None

def _bias_from_trend(df: pd.DataFrame) -> str:
    close = df["close"]
    cloud = ema_cloud(close)
    # Trend bias simple: ema8 > ema21 > ema50 > ema200 -> LONG, inverse -> SHORT, sinon LONG neutre
    e8, e21, e50, e200 = _last(cloud["ema8"]), _last(cloud["ema21"]), _last(cloud["ema50"]), _last(cloud["ema200"])
    if e8 > e21 > e50 > e200:
        return "LONG"
    if e8 < e21 < e50 < e200:
        return "SHORT"
    # fallback: momentum court terme
    return "LONG" if e8 >= e21 else "SHORT"

def _confluence_score(inst: dict, liq: dict, micro: dict, adx_val: float, squeeze_flag: int, obv_slope: float, hvp: float, bias: str) -> Tuple[int, list]:
    score = 0; reasons=[]
    # Institutionnel
    si = int(inst.get("institutional_score", 0))
    score += si; 
    if si>=2: reasons.append(f"Inst {si}/3")

    # Liquidations: cluster proche dans la direction (pour LONG: cluster au-dessus -> aimant)
    cl = liq.get("nearest_cluster")
    if cl:
        dist = float(cl["distance"]); price = float(cl["price"])
        reasons.append(f"Liq cluster @ {price:.4g}, dist {dist:.4g}")
        # si cluster à <1.5%: +1
        if dist / max(price, 1e-9) < 0.015:
            score += 1

    # Microstructure
    if micro.get("ok"):
        imb = float(micro["imbalance"])
        # pour LONG on veut imbalance <= 0 (bid > ask), pression < 0
        if (bias=="LONG" and imb < 0) or (bias=="SHORT" and imb > 0):
            score += 1; reasons.append("Orderbook favorable")
        else:
            reasons.append("Orderbook neutre/défavorable")

    # ADX / trend
    if adx_val >= 20:
        score += 1; reasons.append(f"ADX {adx_val:.1f}")
    # Squeeze
    if squeeze_flag == 1:
        reasons.append("Squeeze ON (pré-explosion)")
    # OBV slope (derivée simple)
    if obv_slope > 0 and bias=="LONG":
        score += 1; reasons.append("OBV↑")
    if obv_slope < 0 and bias=="SHORT":
        score += 1; reasons.append("OBV↓")

    # HV percentile: faible (compression) avant expansion
    if hvp <= 40:
        score += 1; reasons.append(f"HV% {hvp:.0f}")

    return score, reasons

def generate_trade_candidate(symbol: str, df: pd.DataFrame) -> Tuple[Optional[dict], Optional[str], dict]:
    """
    Retourne (signal, err, debug_info)
    signal -> {symbol, bias, entry, sl, tp1, tp2, rr_estimated, df, ote}
    """
    if df.empty or df.shape[0] < 60:
        return None, "Historique insuffisant", {}

    bias = _bias_from_trend(df)
    entry = float(df["close"].iloc[-1])

    # Blocks de features
    inst = compute_full_institutional_analysis(symbol, bias)

    liq = fetch_liquidations_heatmap(symbol, limit=1000, bucket_pct=0.002)
    micro = fetch_orderbook_snapshot(symbol, depth=50)

    pdi, mdi, adx_series = adx(df, 14)
    adx_val = float(adx_series.iloc[-1]) if np.isfinite(adx_series.iloc[-1]) else 0.0

    sq = int(squeeze_on(df).iloc[-1])
    obv_series = obv(df)
    obv_slope = float(pd.Series(obv_series).diff().iloc[-1]) if len(obv_series) > 5 else 0.0

    hvp = hv_percentile(df["close"], lookback=20, window=20)
    atr_val = float(ind_atr(df, 14).iloc[-1]) if np.isfinite(ind_atr(df,14).iloc[-1]) else max(entry*0.003, 1e-6)

    # RR construction sûre
    rr_pack, err = _build_rr_safe(bias, entry, atr_val, k_sl=1.5, k_tp=2.2)
    if err:
        return None, err, {}

    # Confluence
    conf, why = _confluence_score(inst, liq, micro, adx_val, sq, obv_slope, hvp, bias)

    # Seuil d’armement “top-1”
    # - confluence >= 3
    # - institutionnel >= 2 ou (squeeze ON et HV% <= 40)
    armed = (conf >= 3) and (inst["institutional_score"] >= 2 or (sq == 1 and hvp <= 40))

    if not armed:
        return None, "Confluence insuffisante", {"inst": inst, "liq": liq, "micro": micro, "adx": adx_val, "hvp": hvp, "sq": sq, "why": why}

    signal = {
        "symbol": symbol,
        "bias": bias,
        "entry": entry,
        "sl": rr_pack["sl"],
        "tp1": rr_pack["tp1"],
        "tp2": rr_pack["tp2"],
        "rr_estimated": rr_pack["rr"],
        "df": df,
        "ote": True,
        "debug": {"conf": conf, "why": why, "adx": adx_val, "hvp": hvp, "sq": sq, "micro": micro, "liq": liq, "inst": inst}
    }
    return signal, None, signal["debug"]
