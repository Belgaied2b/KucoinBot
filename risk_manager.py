# risk_manager.py
def calculate_position_size(account_balance: float,
                            risk_pct: float,
                            atr: float,
                            multiplier: float = 1.0) -> float:
    """
    Taille de position = (account_balance * risk_pct) / (ATR * multiplier)
    """
    risk_amount = account_balance * risk_pct
    return risk_amount / (atr * multiplier)
