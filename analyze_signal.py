from indicators import compute_rsi, compute_macd, compute_ema, compute_atr, is_momentum_ok
from structure_utils import structure_valid
from institutional_data import compute_full_institutional_analysis
from settings import (
    MIN_INST_SCORE, REQUIRE_STRUCTURE, REQUIRE_MOMENTUM,
    RR_MIN_STRICT, RR_MIN_TOLERATED_WITH_INST
)

def evaluate_signal(signal: dict):
    """
    signal attendu:
      {symbol, bias('LONG'/'SHORT'), rr_estimated: float, df: DataFrame, entry, sl, tp1, tp2, ote: bool}

    Politique d'acceptation (top-1 desk):
      1) Institutionnel prioritaire:
           - inst_score >= MIN_INST_SCORE  ET  RR >= RR_MIN_TOLERATED_WITH_INST
           - + structure & momentum exigés si REQUIRE_* = True
      2) Sinon logique stricte:
           - RR >= RR_MIN_STRICT
           - structure & momentum exigés si REQUIRE_* = True
      OTE manquant = toléré (mais on le note)
    """
    df = signal.get("df")
    close = df["close"]
    vol = df["volume"] if "volume" in df else None
    symbol = signal["symbol"]
    bias = signal.get("bias", "LONG").upper()
    rr = float(signal.get("rr_estimated", 0.0) or 0.0)

    # Institutionnel
    inst = compute_full_institutional_analysis(symbol, bias)
    inst_score = inst["institutional_score"]

    # Technique
    struct_ok = structure_valid(df, bias) if REQUIRE_STRUCTURE else True
    mom_ok = (is_momentum_ok(close, vol) if vol is not None else True) if REQUIRE_MOMENTUM else True

    reasons = []

    # Règle A : institutionnel prioritaire
    if inst_score >= MIN_INST_SCORE and rr >= RR_MIN_TOLERATED_WITH_INST:
        if struct_ok and mom_ok:
            return {
                "valid": True,
                "score": 100 + inst_score * 5,
                "rr": rr,
                "reasons": [f"Institutionnel {inst_score}/3 prioritaire"],
                "institutional": inst
            }
        else:
            if not struct_ok: reasons.append("Structure invalide")
            if not mom_ok: reasons.append("Momentum faible")

    # Règle B : stricte (technique + RR élevé)
    if rr >= RR_MIN_STRICT and struct_ok and mom_ok:
        return {
            "valid": True,
            "score": 95 + inst_score * 3,
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

    ote = signal.get("ote", True)
    if not ote:
        reasons.append("OTE manquant (toléré)")

    return {
        "valid": False,
        "score": 50 + inst_score * 2,
        "rr": rr,
        "reasons": reasons,
        "institutional": inst
    }
