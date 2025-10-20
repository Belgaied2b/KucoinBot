"""
institutional_data.py
Combine Funding, Open Interest, Liquidations, et CVD pour calculer un score institutionnel global.
"""
from institutional_live import compute_institutional_score

def compute_full_institutional_analysis(symbol: str, bias: str, prev_oi: float = None):
    inst = compute_institutional_score(symbol, bias, prev_oi)
    details = inst["scores"]
    total = inst["score_total"]

    comment = []
    if details["oi"]: comment.append("Open Interest ↑")
    if details["fund"]: comment.append("Funding cohérent")
    if details["cvd"]: comment.append("CVD cohérent")

    comment_str = ", ".join(comment) if comment else "Aucun flux institutionnel fort"
    signal_strength = "Fort" if total == 3 else "Moyen" if total == 2 else "Faible"

    return {
        "institutional_score": total,
        "institutional_strength": signal_strength,
        "institutional_comment": comment_str,
        "details": inst
    }
