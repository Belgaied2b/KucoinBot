# kucoin_guard.py — sanity check
def maintenance_ok(entry: float, sl: float, side: str, leverage: float, maint_margin_rate: float=0.005) -> bool:
    """
    Garde-fou très approximatif: distance du SL à un prix de liquidation simplifié.
    """
    side = side.lower()
    risk = abs(entry - sl)
    # proxy liquidation distance = entry * maint_margin_rate / leverage
    liq_dist = entry * maint_margin_rate / max(1e-9, leverage)
    return risk > 1.2 * liq_dist
