"""
analyze_signal.py — intégration institutionnelle + garde-fou RR + HTF/BOS quality/Commitment
- Recalcule RR si rr_estimated manquant à partir de (entry, sl, tp1) ; sinon rejette si RR non fini ou <=0
- Priorité institutionnelle (MIN_INST_SCORE) + tolérance RR si inst OK et commitment OK
- Contrôles supplémentaires : HTF alignment, BOS quality (vol+OI), commitment score (OI + CVD)
"""
from __future__ import annotations
import math
from typing import Optional, Dict, Any
import pandas as pd

# Techniques & structure
from indicators import compute_rsi, compute_macd, compute_ema, compute_atr, is_momentum_ok
from structure_utils import structure_valid, htf_trend_ok, bos_quality_ok, commitment_score

# Institutionnel
from institutional_data import compute_full_institutional_analysis
# (optionnel, conservé si tu l'utilises quelque part ailleurs)
try:
    from institutional_data import detect_liquidity_clusters  # noqa: F401
except Exception:
    pass

# Settings (avec valeurs de secours)
from settings import (
    MIN_INST_SCORE, REQUIRE_STRUCTURE, REQUIRE_MOMENTUM,
    RR_MIN_STRICT, RR_MIN_TOLERATED_WITH_INST
)
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


def _get_series(signal: Dict[str, Any], key: str) -> Optional[pd.Series]:
    s = signal.get(key)
    return s if isinstance(s, pd.Series) else None


def _safe_rr(signal: Dict[str, Any]) -> Optional[float]:
    """
    Tente de produire un RR utilisable :
      1) utilise rr_estimated si valable
      2) sinon recalcule: RR = |tp1-entry| / |entry - sl|
    Retourne None si impossible/invalide.
    """
    rr = signal.get("rr_estimated", None)
    if rr is not None:
        try:
            rr = float(rr)
            if math.isfinite(rr) and rr > 0:
                return rr
        except Exception:
            pass

    # Recalcule depuis entry/sl/tp1 si dispo
    try:
        entry = float(signal.get("entry"))
        sl = float(signal.get("sl"))
        tp1 = float(signal.get("tp1"))
        r = abs(entry - sl)
        if r <= 0:
            return None
        rr_calc = abs(tp1 - entry) / r
        return rr_calc if math.isfinite(rr_calc) and rr_calc > 0 else None
    except Exception:
        return None


def _inst_or_neutral(symbol: str, bias: str) -> Dict[str, Any]:
    """
    Appelle compute_full_institutional_analysis en tolérant les erreurs et
    en fournissant un résultat neutre si nécessaire.
    """
    try:
        inst = compute_full_institutional_analysis(symbol, bias)
        if not isinstance(inst, dict):
            return {"institutional_score": 0, "neutral": True, "reason": "invalid_response"}
        # Normalise quelques clés possibles
        if "institutional_score" not in inst:
            inst["institutional_score"] = inst.get("score", 0) or 0
        if "neutral" not in inst:
            inst["neutral"] = False
        return inst
    except Exception as e:
        return {"institutional_score": 0, "neutral": True, "reason": f"exception:{e}"}


def evaluate_signal(signal: Dict[str, Any]) -> Dict[str, Any]:
    """
    signal attendu:
      {
        symbol, bias('LONG'/'SHORT'),
        rr_estimated: float (optionnel),
        df: DataFrame(1H), entry, sl, tp1, tp2, ote: bool,
        # optionnels:
        df_h4: DataFrame(4H) pour HTF align,
        oi_series: pd.Series (OI), cvd_series: pd.Series (CVD)
      }

    Politique d'acceptation (desk pro):
      A) Institutionnel prioritaire:
         - inst_score >= MIN_INST_SCORE
         - commitment_score >= COMMITMENT_MIN
         - RR >= RR_MIN_TOLERATED_WITH_INST
         - + structure/momentum/HTF/BOS-quality selon REQUIRE_*
      B) Sinon stricte:
         - RR >= RR_MIN_STRICT
         - + structure/momentum/HTF/BOS-quality selon REQUIRE_*
      * OTE manquant = toléré (signal noté, pas bloquant)
    """
    # ------------- Données de base -------------
    symbol = signal.get("symbol")
    bias = str(signal.get("bias", "LONG")).upper()
    df = signal.get("df")
    if not isinstance(df, pd.DataFrame) or "close" not in df:
        return {
            "valid": False,
            "score": 0,
            "rr": None,
            "reasons": ["DF introuvable ou invalide"],
            "institutional": {"institutional_score": 0, "neutral": True, "reason": "no_df"}
        }

    close = df["close"]
    vol = df["volume"] if "volume" in df else None

    # ------------- RR garde-fou -------------
    rr = _safe_rr(signal)
    if rr is None or not math.isfinite(rr) or rr <= 0:
        return {
            "valid": False,
            "score": 50,
            "rr": None,
            "reasons": ["RR invalide (entry/SL/TP incohérents)"],
            "institutional": _inst_or_neutral(symbol, bias)
        }

    # ------------- Institutionnel -------------
    inst = _inst_or_neutral(symbol, bias)
    inst_score = int(inst.get("institutional_score", 0))

    # ------------- Techniques -------------
    struct_ok = structure_valid(df, bias) if REQUIRE_STRUCTURE else True
    mom_ok = (is_momentum_ok(close, vol) if vol is not None else True) if REQUIRE_MOMENTUM else True

    # ------------- Garde-fous insto étendus -------------
    df_h4 = signal.get("df_h4")
    htf_ok = htf_trend_ok(df_h4, bias) if REQUIRE_HTF_ALIGN else True

    oi_series = _get_series(signal, "oi_series")
    bos_ok_q = bos_quality_ok(df, oi_series) if REQUIRE_BOS_QUALITY else True

    cvd_series = _get_series(signal, "cvd_series")
    comm = float(commitment_score(oi_series, cvd_series) or 0.0)  # 0..1

    reasons = []

    # ------------- Règle A : institutionnel prioritaire -------------
    if inst_score >= MIN_INST_SCORE and comm >= COMMITMENT_MIN and rr >= RR_MIN_TOLERATED_WITH_INST:
        if struct_ok and mom_ok and htf_ok and bos_ok_q:
            return {
                "valid": True,
                "score": 100 + inst_score * 5 + int(20 * comm),
                "rr": float(rr),
                "reasons": [f"Institutionnel {inst_score}/3 + commitment {comm:.2f}"],
                "institutional": inst
            }
        # expliquer ce qui manque
        if not struct_ok: reasons.append("Structure invalide")
        if not mom_ok: reasons.append("Momentum faible")
        if not htf_ok: reasons.append("HTF non aligné")
        if not bos_ok_q: reasons.append("Break faible (vol/OI)")

    # ------------- Règle B : stricte (technique+RR) -------------
    if rr >= RR_MIN_STRICT and struct_ok and mom_ok and htf_ok and bos_ok_q:
        return {
            "valid": True,
            "score": 95 + inst_score * 3 + int(10 * comm),
            "rr": float(rr),
            "reasons": [f"RR≥{RR_MIN_STRICT} et technique OK"],
            "institutional": inst
        }

    # ------------- Rejet : raisons détaillées -------------
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

    ote = bool(signal.get("ote", True))
    if not ote:
        reasons.append("OTE manquant (toléré)")

    return {
        "valid": False,
        "score": 50 + inst_score * 2 + int(10 * comm),
        "rr": float(rr),
        "reasons": reasons,
        "institutional": inst
    }
