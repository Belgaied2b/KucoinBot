# risk_manager.py

def calculate_position_size(account_balance: float,
                            risk_pct: float,
                            atr: float,
                            multiplier: float = 1.0) -> float:
    """
    Calcule la taille de position :
    Taille = (capital * % risque) / (ATR * multiplicateur)
    """
    risk_amount = account_balance * risk_pct
    return risk_amount / (atr * multiplier)


def calculate_rr(entry: float,
                 sl: float,
                 rr_ratio: float = 2.5,
                 direction: str = "long") -> float:
    """
    Calcule le Take Profit (TP) en fonction d’un Risk/Reward donné.
    """
    risk = abs(entry - sl)

    if direction.lower() == "long":
        tp = entry + (risk * rr_ratio)
    else:
        tp = entry - (risk * rr_ratio)

    return tp
