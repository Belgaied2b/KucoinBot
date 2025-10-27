# stops.py — SL "desk pro": Liquidité > Structure > ATR (fallback)
from __future__ import annotations
from typing import Optional, List
import numpy as np
import pandas as pd

from indicators_true_atr import atr_wilder
from institutional_data import detect_liquidity_clusters
from settings import (
    ATR_LEN, ATR_MULT_SL, STRUCT_LOOKBACK,
    SL_BUFFER_PCT, SL_BUFFER_TICKS,
)

# --- Options supplémentaires (fallback si absentes dans settings.py) ---
try:
    from settings import MAX_SL_PCT
except Exception:
    MAX_SL_PCT = 0.06  # 6% par défaut

try:
    from settings import MIN_SL_TICKS
except Exception:
    MIN_SL_TICKS = 2   # au moins 2 ticks

try:
    from settings import ATR_MULT_SL_CAP
except Exception:
    ATR_MULT_SL_CAP = 2.0  # SL ne dépasse pas 2x l'ATR

# Buffers spécifiques "liquidité" (si non fournis, on réutilise buffers SL)
try:
    from settings import LIQ_LOOKBACK
except Exception:
    LIQ_LOOKBACK = max(40, int(STRUCT_LOOKBACK))

try:
    from settings import LIQ_BUFFER_PCT
except Exception:
    LIQ_BUFFER_PCT = max(0.0, float(SL_BUFFER_PCT))  # par défaut = buffer SL

try:
    from settings import LIQ_BUFFER_TICKS
except Exception:
    LIQ_BUFFER_TICKS = max(2, int(SL_BUFFER_TICKS + 1))  # un chouïa plus loin que SL buffer

# ---------------------------------------------------------------------


def _round_to_tick(x: float, tick: float) -> float:
    """Arrondi conforme au tick. Laisse x inchangé si tick <= 0."""
    if tick <= 0:
        return float(x)
    steps = round(float(x) / float(tick))
    return round(steps * float(tick), 12)


def _safe_last(series: pd.Series) -> Optional[float]:
    try:
        v = float(series.iloc[-1])
        return None if np.isnan(v) or np.isinf(v) else v
    except Exception:
        return None


def _fallback_atr(df: pd.DataFrame, period: int) -> float:
    """
    Fallback ATR simple si atr_wilder renvoie NaN/None.
    TR = max(h-l, |h-c_prev|, |l-c_prev|)
    """
    try:
        h, l, c = df["high"].astype(float), df["low"].astype(float), df["close"].astype(float)
        prev_c = c.shift(1)
        tr = pd.concat([(h - l).abs(), (h - prev_c).abs(), (l - prev_c).abs()], axis=1).max(axis=1)
        atr = tr.rolling(int(max(2, period))).mean()
        v = _safe_last(atr)
        return float(v) if v is not None and v > 0 else 0.0
    except Exception:
        return 0.0


def _swing_low(df: pd.DataFrame, lookback: int) -> Optional[float]:
    """Plus bas de structure sur 'lookback' bougies, en excluant la bougie courante (iloc[-2])."""
    try:
        s = df["low"].rolling(int(max(2, lookback))).min()
        v = float(s.iloc[-2])
        return None if np.isnan(v) or np.isinf(v) else v
    except Exception:
        return None


def _swing_high(df: pd.DataFrame, lookback: int) -> Optional[float]:
    """Plus haut de structure sur 'lookback' bougies, en excluant la bougie courante (iloc[-2])."""
    try:
        s = df["high"].rolling(int(max(2, lookback))).max()
        v = float(s.iloc[-2])
        return None if np.isnan(v) or np.isinf(v) else v
    except Exception:
        return None


def _compute_atr(df: pd.DataFrame) -> float:
    """ATR Wilder avec fallback robuste."""
    try:
        atr_series = atr_wilder(df, int(ATR_LEN))
        atr_val = _safe_last(atr_series)
        if atr_val is None or atr_val <= 0:
            atr_val = _fallback_atr(df, int(ATR_LEN))
    except Exception:
        atr_val = _fallback_atr(df, int(ATR_LEN))
    return float(max(0.0, atr_val or 0.0))


# --------------------------- Liquidité ---------------------------

def _choose_nearest_liquidity_below(levels: List[float], entry: float) -> Optional[float]:
    """Pour un LONG: prend le niveau de liquidité (< entry) le plus proche de l'entrée."""
    below = [float(x) for x in levels if float(x) < float(entry)]
    if not below:
        return None
    # le plus grand < entry (donc le plus proche)
    return max(below)

def _choose_nearest_liquidity_above(levels: List[float], entry: float) -> Optional[float]:
    """Pour un SHORT: prend le niveau de liquidité (> entry) le plus proche de l'entrée."""
    above = [float(x) for x in levels if float(x) > float(entry)]
    if not above:
        return None
    # le plus petit > entry (donc le plus proche)
    return min(above)


def _liquidity_based_stop_long(df: pd.DataFrame, entry: float, tick: float) -> Optional[float]:
    """
    SL long prioritaire: sous la liquidité (equal lows) la plus proche sous l'entrée.
    Renvoie None si aucune liquidité exploitable.
    """
    try:
        liq = detect_liquidity_clusters(df, lookback=int(LIQ_LOOKBACK), tolerance=0.0005)
        eq_lows = list(liq.get("eq_lows", []))
    except Exception:
        eq_lows = []
    if not eq_lows:
        return None

    lvl = _choose_nearest_liquidity_below(eq_lows, float(entry))
    if lvl is None:
        return None

    # Buffer "insto": pct puis ticks
    base = float(lvl) * (1.0 - float(LIQ_BUFFER_PCT))
    sl = _round_to_tick(base, float(tick)) - float(LIQ_BUFFER_TICKS) * float(tick)
    return float(sl)


def _liquidity_based_stop_short(df: pd.DataFrame, entry: float, tick: float) -> Optional[float]:
    """
    SL short prioritaire: au-dessus de la liquidité (equal highs) la plus proche au-dessus de l'entrée.
    Renvoie None si aucune liquidité exploitable.
    """
    try:
        liq = detect_liquidity_clusters(df, lookback=int(LIQ_LOOKBACK), tolerance=0.0005)
        eq_highs = list(liq.get("eq_highs", []))
    except Exception:
        eq_highs = []
    if not eq_highs:
        return None

    lvl = _choose_nearest_liquidity_above(eq_highs, float(entry))
    if lvl is None:
        return None

    # Buffer "insto": pct puis ticks
    base = float(lvl) * (1.0 + float(LIQ_BUFFER_PCT))
    sl = _round_to_tick(base, float(tick)) + float(LIQ_BUFFER_TICKS) * float(tick)
    return float(sl)


# ------------------------ Garde-fous communs ------------------------

def _apply_common_clamps(entry: float,
                         sl_raw: float,
                         side: str,
                         tick: float,
                         atr_value: float) -> float:
    """
    Applique les garde-fous communs :
    - clamp % max (MAX_SL_PCT)
    - cap ATR (ATR_MULT_SL_CAP)
    - distance minimale en ticks (MIN_SL_TICKS)
    - alignement tick
    - sécurité côté (long: SL < entry ; short: SL > entry)
    """
    side = (side or "").lower()
    tick = float(max(tick, 0.0))
    entry = float(entry)
    sl = float(sl_raw)

    # Cap % distance max
    if MAX_SL_PCT and MAX_SL_PCT > 0:
        max_dist_abs = entry * float(MAX_SL_PCT)
        if abs(entry - sl) > max_dist_abs:
            sl = entry - max_dist_abs if side == "buy" else entry + max_dist_abs

    # Cap ATR absolu
    if ATR_MULT_SL_CAP and ATR_MULT_SL_CAP > 0 and atr_value and atr_value > 0:
        atr_cap = float(atr_value) * float(ATR_MULT_SL_CAP)
        if abs(entry - sl) > atr_cap:
            sl = entry - atr_cap if side == "buy" else entry + atr_cap

    # Distance minimale en ticks
    min_dist = max(float(MIN_SL_TICKS) * tick, tick if tick > 0 else 0.0)
    if abs(entry - sl) < min_dist:
        sl = entry - min_dist if side == "buy" else entry + min_dist

    # Alignement tick
    sl = _round_to_tick(sl, tick)

    # Bon côté après alignement
    if side == "buy":
        sl = min(sl, _round_to_tick(entry - tick, tick))
    else:
        sl = max(sl, _round_to_tick(entry + tick, tick))

    return max(1e-8, float(sl))


# ------------------------ API principale ------------------------

def protective_stop_long(df: pd.DataFrame, entry: float, tick: float) -> float:
    """
    SL LONG — priorité:
      1) Liquidité (equal lows) la plus proche sous l'entrée (+ buffers liq)
      2) Structure: swing low lookback
      3) ATR: entry - ATR_MULT_SL * ATR    (dernier recours)
      Puis garde-fous & alignement tick.
    """
    atr_val = _compute_atr(df)

    # 1) Liquidité prioritaire
    sl_liq = _liquidity_based_stop_long(df, float(entry), float(tick))
    if sl_liq is not None:
        raw = float(sl_liq)
    else:
        # 2) Structure
        swing = _swing_low(df, int(STRUCT_LOOKBACK))
        if swing is None:
            try:
                swing = float(df["low"].iloc[-2])
            except Exception:
                swing = None

        if swing is not None:
            # buffers "SL" (structure) si on passe par swing
            base = float(swing) * (1.0 - float(SL_BUFFER_PCT))
            raw = _round_to_tick(base, float(tick)) - float(SL_BUFFER_TICKS) * float(tick)
        else:
            # 3) Dernier recours: ATR
            sl_atr = float(entry) - float(ATR_MULT_SL) * float(atr_val)
            base = sl_atr * (1.0 - float(SL_BUFFER_PCT))
            raw = _round_to_tick(base, float(tick)) - float(SL_BUFFER_TICKS) * float(tick)

    # Garde-fous communs
    return _apply_common_clamps(entry=float(entry), sl_raw=float(raw), side="buy",
                                tick=float(tick), atr_value=float(atr_val))


def protective_stop_short(df: pd.DataFrame, entry: float, tick: float) -> float:
    """
    SL SHORT — priorité:
      1) Liquidité (equal highs) la plus proche au-dessus de l'entrée (+ buffers liq)
      2) Structure: swing high lookback
      3) ATR: entry + ATR_MULT_SL * ATR    (dernier recours)
      Puis garde-fous & alignement tick.
    """
    atr_val = _compute_atr(df)

    # 1) Liquidité prioritaire
    sl_liq = _liquidity_based_stop_short(df, float(entry), float(tick))
    if sl_liq is not None:
        raw = float(sl_liq)
    else:
        # 2) Structure
        swing = _swing_high(df, int(STRUCT_LOOKBACK))
        if swing is None:
            try:
                swing = float(df["high"].iloc[-2])
            except Exception:
                swing = None

        if swing is not None:
            base = float(swing) * (1.0 + float(SL_BUFFER_PCT))
            raw = _round_to_tick(base, float(tick)) + float(SL_BUFFER_TICKS) * float(tick)
        else:
            # 3) Dernier recours: ATR
            sl_atr = float(entry) + float(ATR_MULT_SL) * float(atr_val)
            base = sl_atr * (1.0 + float(SL_BUFFER_PCT))
            raw = _round_to_tick(base, float(tick)) + float(SL_BUFFER_TICKS) * float(tick)

    # Garde-fous communs
    return _apply_common_clamps(entry=float(entry), sl_raw=float(raw), side="sell",
                                tick=float(tick), atr_value=float(atr_val))
