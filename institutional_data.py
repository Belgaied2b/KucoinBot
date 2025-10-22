"""
Couche d’orchestration institutionnelle (pondération + commentaire).
"""
from institutional_live import compute_institutional_score

def compute_full_institutional_analysis(symbol: str, bias: str, prev_oi: float = None):
    inst = compute_institutional_score(symbol, bias, prev_oi)
    d = inst["scores"]; total = inst["score_total"]
    comment = []
    if d["oi"]: comment.append("OI↑")
    if d["fund"]: comment.append("Funding cohérent")
    if d["cvd"]: comment.append("CVD cohérent")
    strength = "Fort" if total==3 else ("Moyen" if total==2 else "Faible")
    return {
        "institutional_score": total,
        "institutional_strength": strength,
        "institutional_comment": ", ".join(comment) if comment else "Pas de flux dominants",
        "details": inst
    }
