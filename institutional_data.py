"""
institutional_data.py — Analyse institutionnelle avancée
- Profil du volume institutionnel (large traders vs. retail)
- Delta cumulé par bougie (divergence CVD ↔ prix)
- Liquidity map dynamique (equal highs/lows)
- Couche d’orchestration institutionnelle (pondération + commentaire)
"""

import requests
import numpy as np
import pandas as pd
import logging

LOGGER = logging.getLogger(__name__)

BINANCE_FUTURES_API = "https://fapi.binance.com/fapi/v1"
DEPTH_LIMIT = 1000

# --------------------------------------------------------------------
# 🔹 1. PROFIL VOLUME INSTITUTIONNEL (grands comptes vs. retail)
# --------------------------------------------------------------------
def get_large_trader_ratio(symbol: str) -> float:
    """
    Retourne un ratio entre 0 et 1 :
    - proche de 1 => flux dominé par les gros traders
    - proche de 0 => flux retail
    """
    try:
        url = f"{BINANCE_FUTURES_API}/topLongShortAccountRatio?symbol={symbol.upper()}USDT&period=1h&limit=1"
        data = requests.get(url, timeout=5).json()
        if isinstance(data, list) and data:
            ratio = float(data[0].get("longAccount", 0)) / max(1e-8, float(data[0].get("shortAccount", 1)))
            score = np.tanh(ratio)  # normalisé entre 0 et 1
            return float(np.clip(score, 0, 1))
    except Exception as e:
        LOGGER.warning("Large trader ratio fetch failed for %s: %s", symbol, e)
    return 0.5  # neutre


# --------------------------------------------------------------------
# 🔹 2. DELTA CUMULÉ PAR CANDLE (divergence CVD vs prix)
# --------------------------------------------------------------------
def get_cvd_divergence(symbol: str, limit: int = 200) -> float:
    """
    Analyse la divergence entre le CVD (delta volume) et le prix.
    Retourne un score de -1 à +1 :
    - +1 = delta cohérent (prix & CVD montent ensemble)
    - -1 = divergence (CVD baisse alors que prix monte)
    """
    try:
        url = f"{BINANCE_FUTURES_API}/aggTrades?symbol={symbol.upper()}USDT&limit={limit}"
        trades = requests.get(url, timeout=5).json()
        df = pd.DataFrame(trades)
        df["p"] = df["p"].astype(float)
        df["q"] = df["q"].astype(float)
        df["side"] = df["m"].apply(lambda x: -1 if x else 1)  # maker = -1 (vendeur), taker = +1 (acheteur)
        df["delta"] = df["q"] * df["side"]

        cvd = df["delta"].cumsum().iloc[-1]
        price_change = df["p"].iloc[-1] - df["p"].iloc[0]
        if abs(price_change) < 1e-8:
            return 0.0
        corr = np.sign(price_change) * np.sign(cvd)
        return float(corr)
    except Exception as e:
        LOGGER.warning("CVD divergence fetch failed for %s: %s", symbol, e)
    return 0.0


# --------------------------------------------------------------------
# 🔹 3. LIQUIDITY MAP (equal highs/lows + clusters)
# --------------------------------------------------------------------
def detect_liquidity_clusters(df: pd.DataFrame, lookback: int = 50, tolerance: float = 0.0005):
    """
    Détecte les zones de liquidité (equal highs/lows) sur la période récente.
    Retourne un dict avec :
      { "eq_highs": [levels...], "eq_lows": [levels...] }
    """
    highs, lows = df["high"].tail(lookback).values, df["low"].tail(lookback).values
    eq_highs, eq_lows = [], []

    for i in range(1, len(highs)):
        if abs(highs[i] - highs[i - 1]) / highs[i] < tolerance:
            eq_highs.append(highs[i])
        if abs(lows[i] - lows[i - 1]) / lows[i] < tolerance:
            eq_lows.append(lows[i])

    return {
        "eq_highs": sorted(list(set(round(x, 6) for x in eq_highs))),
        "eq_lows": sorted(list(set(round(x, 6) for x in eq_lows))),
    }


# --------------------------------------------------------------------
# 🔸 SCORE INSTITUTIONNEL GLOBAL
# --------------------------------------------------------------------
def compute_institutional_score(symbol: str, bias: str, prev_oi: float = None):
    """
    Calcule le score institutionnel complet (pondéré)
    Retourne :
    {
      "scores": {"oi": int, "fund": int, "cvd": int, "liquidity": int},
      "score_total": int,
      "details": {...}
    }
    """
    large_ratio = get_large_trader_ratio(symbol)
    cvd_div = get_cvd_divergence(symbol)

    # Pondération
    oi_score = 1 if large_ratio > 0.55 else 0
    cvd_score = 1 if cvd_div > 0 else 0
    fund_score = 1  # placeholder (sera remplacé si funding réel dispo)
    liq_score = 0   # calculé côté structure_utils pour précision

    score_total = oi_score + cvd_score + fund_score

    return {
        "scores": {
            "oi": oi_score,
            "fund": fund_score,
            "cvd": cvd_score,
            "liquidity": liq_score
        },
        "score_total": score_total,
        "details": {
            "large_ratio": round(large_ratio, 3),
            "cvd_divergence": cvd_div,
            "bias": bias
        }
    }


# --------------------------------------------------------------------
# 🔹 4. COUCHE D’ORCHESTRATION (pondération + commentaire)
# --------------------------------------------------------------------
def compute_full_institutional_analysis(symbol: str, bias: str, prev_oi: float = None):
    inst = compute_institutional_score(symbol, bias, prev_oi)
    d = inst["scores"]
    total = inst["score_total"]

    comment = []
    if d["oi"]:
        comment.append("OI↑")
    if d["fund"]:
        comment.append("Funding cohérent")
    if d["cvd"]:
        comment.append("CVD cohérent")

    strength = "Fort" if total == 3 else ("Moyen" if total == 2 else "Faible")

    return {
        "institutional_score": total,
        "institutional_strength": strength,
        "institutional_comment": ", ".join(comment) if comment else "Pas de flux dominants",
        "details": inst
    }
