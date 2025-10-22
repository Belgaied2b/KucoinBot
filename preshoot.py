"""
preshoot.py — Pré-shoot institutionnel (anticiper avant la cassure)
- Squeeze ON, ADX slope +, OBV slope +, cluster de liquidations proche, orderbook favorable,
  institutionnel (funding/oi/cvd) >= 2  => probabilité de pré-breakout
- Retourne (probabilité 0..1, early_entry_pack)
"""
from __future__ import annotations
import numpy as np
import pandas as pd
from typing import Dict, Tuple, Optional

from advanced_indicators import adx, squeeze_on, obv, atr as ind_atr
from liquidity_map import fetch_liquidations_heatmap
from microstructure import fetch_orderbook_snapshot
from institutional_data import compute_full_institutional_analysis

def _last(x):
    try:
        return float(x.iloc[-1])
    except Exception:
        return 0.0

def _safe_atr(df: pd.DataFrame) -> float:
    a = _last(ind_atr(df, 14))
    if not np.isfinite(a) or a <= 0:
        a = max(float(df["close"].iloc[-1]) * 0.003, 1e-6)
    return float(a)

def preshoot_probability(symbol: str, df: pd.DataFrame) -> Tuple[float, Optional[Dict]]:
    if df.empty or df.shape[0] < 80:
        return 0.0, None

    close = df["close"]
    # Features
    pdi, mdi, adx_s = adx(df, 14)
    adx_val = _last(adx_s)
    adx_slope = _last(adx_s.diff().rolling(5).mean())
    sq_on = int(_last(squeeze_on(df)))

    obv_s = obv(df)
    obv_slope = _last(pd.Series(obv_s).diff().rolling(5).mean())

    inst = compute_full_institutional_analysis(symbol, "LONG")  # on cherche surtout l’explosion haussière
    inst_score = int(inst.get("institutional_score", 0))

    liq = fetch_liquidations_heatmap(symbol, limit=800, bucket_pct=0.0018)
    micro = fetch_orderbook_snapshot(symbol, depth=50)

    # Scoring simple (0..10)
    score = 0
    if sq_on == 1: score += 2     # compression en cours
    if adx_val >= 18 and adx_slope > 0: score += 2
    if obv_slope > 0: score += 1
    if inst_score >= 2: score += 3
    if liq.get("nearest_cluster"):
        dist = float(liq["nearest_cluster"]["distance"]); ref = float(liq["nearest_cluster"]["price"])
        if dist / max(ref, 1e-9) < 0.012: score += 1
    if micro.get("ok") and (float(micro["imbalance"]) < 0):  # bid side > ask
        score += 1

    prob = min(max(score / 10.0, 0.0), 1.0)

    # Early entry pack (RR>0)
    entry = float(close.iloc[-1]); atr = _safe_atr(df)
    sl = entry - 1.2 * atr
    tp = entry + 2.0 * atr
    stop = entry - sl; prof = tp - entry
    if stop <= 0 or prof <= 0:
        return prob, None
    rr = prof / stop
    pack = {"entry": entry, "sl": sl, "tp": tp, "rr": float(rr)}
    return prob, pack
