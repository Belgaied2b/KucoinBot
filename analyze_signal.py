"""
analyze_signal.py
Analyse complète du signal avec intégration institutionnelle.
"""
from indicators import *
from structure_utils import *
from institutional_live import compute_institutional_score

def evaluate_signal(signal):
    symbol = signal.get("symbol")
    bias = signal.get("bias", "LONG").upper()
    rr = signal.get("rr_estimated", 0.0)
    inst = compute_institutional_score(symbol, bias)
    rr_required = 1.5
    valid = False
    comment = []

    if inst["score_total"] >= 2 and rr >= 1.2:
        valid = True
        comment.append(f"Institutionnel {inst['score_total']}/3 => priorité.")
    elif rr >= rr_required:
        valid = True
    else:
        comment.append(f"RR {rr:.2f} insuffisant.")

    score = 100 + inst["score_total"] * 5 if valid else 50 + inst["score_total"] * 3
    return {
        "valid": valid,
        "score": score,
        "institutional": inst,
        "comment": " | ".join(comment)
    }
