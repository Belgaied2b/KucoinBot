# =====================================================================
# stops.py — Desk Lead Institutional Stop Engine (FULL VERSION)
# Compatible analyze_signal.py (protective_stop_long / protective_stop_short)
# =====================================================================

import pandas as pd
import numpy as np
from typing import Dict, Any, Tuple, Optional

from structure_utils import find_swings, detect_equal_levels


# =====================================================================
# Helper — Round to Tick
# =====================================================================

def _round_to_tick(price: float, tick: float) -> float:
    return round(price / tick) * tick


# =====================================================================
# CORE LOGIC — Protective Stop for LONG
# =====================================================================

def protective_stop_long(df: pd.DataFrame, entry: float, tick: float,
                         return_meta: bool = False) -> Tuple[float, Dict[str, Any]]:
    """
    Stop Loss institutionnel pour un long :
        - sous swing low récent
        - sous blocs de liquidité (equal lows)
        - sous ATR buffer
        - jamais trop loin (clamp max)
    """

    lows = df["low"]
    swings = find_swings(df, left=3, right=3)["lows"]
    liq = detect_equal_levels(df)

    # Dernier swing low propre
    if swings:
        last_swing_low = float(swings[-1][1])
    else:
        last_swing_low = float(lows.tail(10).min())

    # Equal lows (liquidity pools)
    eq_lows = liq.get("eq_lows", [])
    liq_low = min(eq_lows) if eq_lows else last_swing_low

    # ATR buffer
    atr = df["high"].rolling(14).max() - df["low"].rolling(14).min()
    atr_val = float(atr.iloc[-1] if not np.isnan(atr.iloc[-1]) else atr.mean())

    sl_raw = min(last_swing_low, liq_low) - atr_val * 0.2
    sl_final = _round_to_tick(sl_raw, tick)

    meta = {
        "last_swing_low": last_swing_low,
        "liq_low": liq_low,
        "atr": atr_val,
        "sl_raw": sl_raw,
    }

    if return_meta:
        return sl_final, meta
    return sl_final, {}


# =====================================================================
# CORE LOGIC — Protective Stop for SHORT
# =====================================================================

def protective_stop_short(df: pd.DataFrame, entry: float, tick: float,
                          return_meta: bool = False) -> Tuple[float, Dict[str, Any]]:
    """
    Stop Loss institutionnel pour un short :
        - au-dessus dernier swing high
        - au-dessus equal highs (liquidity)
        - ATR buffer
    """

    highs = df["high"]
    swings = find_swings(df, left=3, right=3)["highs"]
    liq = detect_equal_levels(df)

    # Dernier swing high propre
    if swings:
        last_swing_high = float(swings[-1][1])
    else:
        last_swing_high = float(highs.tail(10).max())

    # Equal highs (liquidity pools)
    eq_highs = liq.get("eq_highs", [])
    liq_high = max(eq_highs) if eq_highs else last_swing_high

    # ATR buffer
    atr = df["high"].rolling(14).max() - df["low"].rolling(14).min()
    atr_val = float(atr.iloc[-1] if not np.isnan(atr.iloc[-1]) else atr.mean())

    sl_raw = max(last_swing_high, liq_high) + atr_val * 0.2
    sl_final = _round_to_tick(sl_raw, tick)

    meta = {
        "last_swing_high": last_swing_high,
        "liq_high": liq_high,
        "atr": atr_val,
        "sl_raw": sl_raw,
    }

    if return_meta:
        return sl_final, meta
    return sl_final, {}
