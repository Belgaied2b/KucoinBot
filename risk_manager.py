def calculate_position_size(capital: float, risk_percent: float, entry: float, stop_loss: float) -> float:
    """
    Calcule la taille de position en fonction du capital, du risque et du SL.
    """
    risk_amount = capital * risk_percent
    risk_per_unit = abs(entry - stop_loss)
    if risk_per_unit == 0:
        return 0
    return risk_amount / risk_per_unit

def calculate_rr(entry: float, sl: float, rr_ratio: float = 2.0, direction: str = "long") -> float:
    """
    Calcule le TP à partir d’un R:R donné.
    - LONG : TP = entry + (entry - SL) * ratio
    - SHORT : TP = entry - (SL - entry) * ratio
    """
    if direction == "long":
        return entry + (entry - sl) * rr_ratio
    else:
        return entry - (sl - entry) * rr_ratio
