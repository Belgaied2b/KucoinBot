"""
analyze_signal.py — intégration institutionnelle + garde-fou RR + HTF/BOS quality/Commitment
- Rejette immédiatement tout signal dont le RR n’est pas fini et > 0
- Priorité institutionnelle (MIN_INST_SCORE) + tolérance RR si inst OK
- + HTF alignment, qualité de break (vol+OI), et commitment score (OI + CVD)
"""
import math
from typing import Optional
import pandas as pd

from indicators import compute_rsi, compute_macd, compute_ema, compute_atr, is_momentum_ok
from structure_utils import structure_valid
from institutional_data import compute_full_institutional_analysis
from settings import (
    MIN_INST_SCORE, REQUIRE_STRUCTURE, REQUIRE_MOMENTUM,
    RR_MIN_STRICT, RR_MIN_TOLERATED_WITH_INST
)

# Options supplémentaires (valeurs par défaut si absentes du settings)
try:
    from settings import REQUIRE_HTF_ALIGN
except Exception:
    REQUIRE_HTF_ALIGN = True

try:
    from settings import REQUIRE_BOS_QUALITY
except Exception:
    REQUIRE_BOS_QUALITY = True

try:
    from settings import COMMITMENT_MIN
except Exception:
    COMMITMENT_MIN = 0.55  # seuil 0..1

# Imports des nouveaux helpers (présents dans structure_utils.py)
from structure_utils import htf_trend_ok, bos_quality_ok, commitment_score

def _get_series(signal: dict, key: str) -> Optional[pd.Series]:
    s = signal.get(key)
    return s if isinstance(s, pd.Series) else None

def evaluate_signal(signal: dict):
    """
    signal attendu:
      {
        symbol, bias('LONG'/'SHORT'), rr_estimated: float,
        df: DataFrame(1H), entry, sl, tp1, tp2, ote: bool,
        # optionnels:
        df_h4: DataFrame(4H) pour HTF align,
        oi_series: pd.Series (OI), cvd_series: pd.Series (CVD)
      }

    Politique d'acceptation (desk pro):
      1) Institutionnel prioritaire:
           - inst_score >= MIN_INST_SCORE
           - commitment_score >= COMMITMENT_MIN
           - RR >= RR_MIN_TOLERATED_WITH_INST
           - + structure/momentum/HTF/BOS-quality selon REQUIRE_*
      2) Sinon stricte:
           - RR >= RR_MIN_STRICT
           - + structure/momentum/HTF/BOS-quality selon REQUIRE_*
      OTE manquant = toléré (signal noté, pas bloquant)
    """
    df = signal.get("df")
    close = df["close"]
    vol = df["volume"] if "volume" in df else None
    symbol = signal["symbol"]
    bias = signal.get("bias", "LONG").upper()
    rr = float(signal.get("rr_estimated", 0.0) or 0.0)

    # --- Garde-fou RR ---
    if rr is None or not math.isfinite(rr) or rr <= 0:
        return {
            "valid": False,
            "score": 50,
            "rr": None,
            "reasons": ["RR invalide (entry/SL/TP incohérents)"],
            "institutional": compute_full_institutional_analysis(symbol, bias)
        }

    # Institutionnel (live, agrégé)
    inst = compute_full_institutional_analysis(symbol, bias)
    inst_score = inst["institutional_score"]

    # Techniques de base
    struct_ok = structure_valid(df, bias) if REQUIRE_STRUCTURE else True
    mom_ok = (is_momentum_ok(close, vol) if vol is not None else True) if REQUIRE_MOMENTUM else True

    # Nouveaux garde-fous “insto”
    df_h4 = signal.get("df_h4")
    htf_ok = htf_trend_ok(df_h4, bias) if REQUIRE_HTF_ALIGN else True

    oi_series = _get_series(signal, "oi_series")
    bos_ok_q = bos_quality_ok(df, oi_series) if REQUIRE_BOS_QUALITY else True

    cvd_series = _get_series(signal, "cvd_series")
    comm = commitment_score(oi_series, cvd_series)  # 0..1

    reasons = []

    # Règle A : institutionnel prioritaire (inst + commitment + RR toléré)
    if inst_score >= MIN_INST_SCORE and comm >= COMMITMENT_MIN and rr >= RR_MIN_TOLERATED_WITH_INST:
        if struct_ok and mom_ok and htf_ok and bos_ok_q:
            return {
                "valid": True,
                "score": 100 + inst_score * 5 + int(20 * comm),
                "rr": rr,
                "reasons": [f"Institutionnel {inst_score}/3 + commitment {comm:.2f}"],
                "institutional": inst
            }
        else:
            if not struct_ok: reasons.append("Structure invalide")
            if not mom_ok: reasons.append("Momentum faible")
            if not htf_ok: reasons.append("HTF non aligné")
            if not bos_ok_q: reasons.append("Break faible (vol/OI)")

    # Règle B : stricte (technique + RR élevé)
    if rr >= RR_MIN_STRICT and struct_ok and mom_ok and htf_ok and bos_ok_q:
        return {
            "valid": True,
            "score": 95 + inst_score * 3 + int(10 * comm),
            "rr": rr,
            "reasons": [f"RR≥{RR_MIN_STRICT} et technique OK"],
            "institutional": inst
        }

    # Sinon: rejet, avec explications
    if rr < RR_MIN_TOLERATED_WITH_INST:
        reasons.append(f"RR {rr:.2f} < toléré {RR_MIN_TOLERATED_WITH_INST}")
    elif rr < RR_MIN_STRICT:
        reasons.append(f"RR {rr:.2f} < strict {RR_MIN_STRICT}")

    if REQUIRE_STRUCTURE and not struct_ok:
        reasons.append("Structure invalide")
    if REQUIRE_MOMENTUM and not mom_ok:
        reasons.append("Momentum faible")
    if REQUIRE_HTF_ALIGN and not htf_ok:
        reasons.append("HTF non aligné")
    if REQUIRE_BOS_QUALITY and not bos_ok_q:
        reasons.append("Break faible (vol/OI)")

    ote = signal.get("ote", True)
    if not ote:
        reasons.append("OTE manquant (toléré)")

    return {
        "valid": False,
        "score": 50 + inst_score * 2 + int(10 * comm),
        "rr": rr,
        "reasons": reasons,
        "institutional": inst
    }
