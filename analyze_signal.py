from indicators import compute_rsi, compute_macd, compute_ema, compute_atr, is_momentum_ok
from structure_utils import structure_valid
from institutional_data import compute_full_institutional_analysis

def evaluate_signal(signal: dict):
    """
    signal attendu:
      {symbol, bias('LONG'/'SHORT'), rr_estimated: float, df: DataFrame, entry, sl, tp1, tp2, ote: bool}
    Règles:
      - Institutionnel prioritaire: score >=2 ET RR>=1.2 => Accept
      - Sinon: logique technique + RR>=1.5, OTE toléré, momentum requis
    """
    df = signal.get("df"); close = df["close"]
    symbol = signal["symbol"]; bias = signal.get("bias","LONG").upper()
    rr = float(signal.get("rr_estimated", 0.0) or 0.0)

    # Institutionnel
    inst = compute_full_institutional_analysis(symbol, bias)
    inst_score = inst["institutional_score"]

    # Technique
    struct_ok = structure_valid(df, bias)
    mom_ok = is_momentum_ok(close, df["volume"]) if "volume" in df else True

    reasons = []
    # Règle prioritaire
    if inst_score >= 2 and rr >= 1.2:
        return {"valid": True, "score": 100 + inst_score*5, "rr": rr, "reasons": ["Institutionnel prioritaire"], "institutional": inst}

    # Sinon logique stricte
    if not struct_ok: reasons.append("Structure invalide")
    if not mom_ok: reasons.append("Momentum faible")

    rr_required = 1.5
    if rr < rr_required:
        if inst_score >= 2 and rr >= 1.2:
            reasons.append(f"RR {rr:.2f} toléré par institutionnel {inst_score}")
        else:
            reasons.append(f"RR insuffisant ({rr:.2f} < {rr_required})")

    ote = signal.get("ote", True)
    if not ote:
        reasons.append("OTE manquant (toléré)")

    valid = (not reasons) or (len(reasons)==1 and "OTE" in reasons[0])  # Tout est OK sauf OTE
    score = (100 if valid else 50) + inst_score*3
    return {"valid": valid, "score": score, "rr": rr, "reasons": reasons, "institutional": inst}
