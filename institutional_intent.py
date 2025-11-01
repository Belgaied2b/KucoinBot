# institutional_intent.py
from __future__ import annotations
from typing import Dict, Any, Optional
import math

from institutional_data import (
    get_large_trader_ratio,     # 0..1 (gros comptes actifs)
    get_cvd_divergence,         # -1..+1 (cohérence CVD/prix)
    detect_liquidity_clusters,  # eq highs/lows sur df local (1H)
)

# (optionnel) funding via premiumIndex — si tu veux l’exploiter plus tard
try:
    import requests
    def _get_premium_index(symbol_binance: str) -> Optional[float]:
        url = "https://fapi.binance.com/fapi/v1/premiumIndex"
        try:
            r = requests.get(url, params={"symbol": symbol_binance}, timeout=4)
            if r.status_code == 200:
                data = r.json()
                return float(data.get("lastFundingRate", 0.0))
        except Exception:
            pass
        return None
except Exception:
    def _get_premium_index(symbol_binance: str) -> Optional[float]:
        return None


def _normalize(x: float, a: float, b: float) -> float:
    # map x from [a,b] to [0,1]
    if b == a:
        return 0.0
    t = (x - a) / (b - a)
    return max(0.0, min(1.0, t))


def compute_institutional_intent(symbol: str, df_1h, *, binance_symbol: str | None = None) -> Dict[str, Any]:
    """
    Renvoie:
      {
        "state": "accumulation"|"distribution"|"neutral",
        "score": 0..100,
        "components": { "large_traders":0..1, "cvd":-1..1, "liq_pull":0..1, "premium":float|None },
        "comment": "…"
      }
    """
    # 1) Gros comptes
    large = float(get_large_trader_ratio(symbol))  # 0..1

    # 2) CVD divergence/cohérence
    cvd = float(get_cvd_divergence(symbol))        # -1..+1

    # 3) Proximité de liquidité (plus on est proche d’un pool, plus la proba d’intervention ↑)
    liq = detect_liquidity_clusters(df_1h, lookback=60, tolerance=0.0005)
    try:
        last = float(df_1h["close"].iloc[-1])
        # prox = 1 si très proche d’un pool (±0.15%), 0 si loin (>1.5%)
        def _prox(levels):
            if not levels:
                return 0.0
            dist = min(abs(last - lv) / max(1e-12, last) for lv in levels)
            return 1.0 - _normalize(dist, 0.0015, 0.015)
        liq_pull = max(_prox(liq["eq_highs"]), _prox(liq["eq_lows"]))  # 0..1
    except Exception:
        liq_pull = 0.0

    # 4) Premium/funding (facultatif, ne pénalise pas s’il est None)
    premium = None
    if binance_symbol:
        premium = _get_premium_index(binance_symbol)

    # ---- Scoring
    # Accumulation: gros comptes actifs + CVD cohérent (+ proximité liq)
    acc = 0.55 * large + 0.35 * max(0.0, cvd) + 0.10 * liq_pull
    # Distribution: gros comptes + CVD à contre-sens (cvd<0)
    dist = 0.55 * large + 0.35 * max(0.0, -cvd) + 0.10 * liq_pull

    acc_s = int(round(acc * 100))
    dist_s = int(round(dist * 100))

    if abs(acc_s - dist_s) <= 7:   # zone grise
        state = "neutral"
        score = max(acc_s, dist_s)
    else:
        state = "accumulation" if acc_s > dist_s else "distribution"
        score = max(acc_s, dist_s)

    comment = []
    comment.append(f"large={large:.2f}")
    comment.append(f"cvd={cvd:+.2f}")
    comment.append(f"liq_pull={liq_pull:.2f}")
    if premium is not None:
        comment.append(f"premium={premium:+.4f}")

    return {
        "state": state,
        "score": int(score),
        "components": {
            "large_traders": round(large, 3),
            "cvd": round(cvd, 3),
            "liq_pull": round(liq_pull, 3),
            "premium": premium,
        },
        "comment": ", ".join(comment),
    }
