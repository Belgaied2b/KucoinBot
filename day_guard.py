# day_guard.py — stop journalier
import time
from typing import Dict

DAY_PNL = {"ts":0, "realized":0.0, "fees":0.0}
DAY_LIMIT_USDT = 3 * 20.0  # ex: 3 * marge fixe (à régler dans settings)

def reset_if_new_day():
    t = time.gmtime()
    day = (t.tm_year, t.tm_yday)
    if DAY_PNL["ts"] != day:
        DAY_PNL["ts"] = day
        DAY_PNL["realized"] = 0.0
        DAY_PNL["fees"] = 0.0

def register_fill_pnl(pnl_usdt: float, fees_usdt: float=0.0):
    reset_if_new_day()
    DAY_PNL["realized"] += float(pnl_usdt)
    DAY_PNL["fees"] += float(fees_usdt)

def day_guard_ok() -> tuple[bool,str]:
    reset_if_new_day()
    loss = -(DAY_PNL["realized"] - DAY_PNL["fees"])
    return (loss <= DAY_LIMIT_USDT, f"day loss {loss:.2f}/{DAY_LIMIT_USDT:.2f}")
