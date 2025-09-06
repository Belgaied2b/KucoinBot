
from __future__ import annotations

def valueqty_from_risk(entry: float, sl: float, risk_usdt: float) -> float:
    e = float(entry); s = float(sl); r = float(risk_usdt)
    if e <= 0:
        raise ValueError("entry must be > 0")
    dist = abs(e - s)
    if dist <= 0:
        raise ValueError("SL distance must be > 0")
    return (r * e) / dist
