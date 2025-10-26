# stops.py — stops structure + ATR + buffers (avec clamps pro)
from __future__ import annotations
from typing import Optional
import numpy as np
import pandas as pd

from indicators_true_atr import atr_wilder
from settings import ATR_LEN, ATR_MULT_SL, STRUCT_LOOKBACK, SL_BUFFER_PCT, SL_BUFFER_TICKS

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

# ---------------------------------------------------------------------


def _round_to_tick(x: float, tick: float) -> float:
    """
    Arrondi conforme au tick. Laisse x inchangé si tick <= 0.
    """
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
    """
    Plus bas de structure sur 'lookback' bougies, en excluant la bougie courante (iloc[-2]).
    """
    try:
        s = df["low"].rolling(int(max(2, lookback))).min()
        v = float(s.iloc[-2])
        return None if np.isnan(v) or np.isinf(v) else v
    except Exception:
        return None


def _swing_high(df: pd.DataFrame, lookback: int) -> Optional[float]:
    """
    Plus haut de structure sur 'lookback' bougies, en excluant la bougie courante (iloc[-2]).
    """
    try:
        s = df["high"].rolling(int(max(2, lookback))).max()
        v = float(s.iloc[-2])
        return None if np.isnan(v) or np.isinf(v) else v
    except Exception:
        return None


def _compute_atr(df: pd.DataFrame) -> float:
    """
    ATR Wilder avec fallback robuste.
    """
    try:
        atr_series = atr_wilder(df, int(ATR_LEN))
        atr_val = _safe_last(atr_series)
        if atr_val is None or atr_val <= 0:
            atr_val = _fallback_atr(df, int(ATR_LEN))
    except Exception:
        atr_val = _fallback_atr(df, int(ATR_LEN))
    return float(max(0.0, atr_val or 0.0))


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
    side = side.lower()
    tick = float(max(tick, 0.0))
    entry = float(entry)
    sl = float(sl_raw)

    # Clamp % max (distance SL-Entry)
    if MAX_SL_PCT and MAX_SL_PCT > 0:
        max_dist_abs = entry * float(MAX_SL_PCT)
        if abs(entry - sl) > max_dist_abs:
            sl = entry - max_dist_abs if side == "buy" else entry + max_dist_abs

    # ATR cap (indépendant de ATR_MULT_SL utilisé pour la base)
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

    # Sécurité ultime : bon côté après alignement
    if side == "buy":
        sl = min(sl, _round_to_tick(entry - tick, tick))  # SL strictement sous l'entrée
    else:
        sl = max(sl, _round_to_tick(entry + tick, tick))  # SL strictement au-dessus de l'entrée

    # Evite valeurs absurdes/négatives
    return max(1e-8, float(sl))


def protective_stop_long(df: pd.DataFrame, entry: float, tick: float) -> float:
    """
    SL LONG “insto”:
      base = min( swing_low, entry - ATR_MULT_SL * ATR )
      puis buffers:
        - pourcentage: base * (1 - SL_BUFFER_PCT)
        - ticks:       - SL_BUFFER_TICKS * tick
      ensuite clamps: MAX_SL_PCT, ATR_MULT_SL_CAP, MIN_SL_TICKS, alignement tick, côté.
    """
    atr_val = _compute_atr(df)

    # Structure (fallback sur low[-2] si rolling indispo)
    swing = _swing_low(df, int(STRUCT_LOOKBACK))
    if swing is None:
        try:
            swing = float(df["low"].iloc[-2])
        except Exception:
            swing = entry  # worst-case fallback (corrigé par clamps ensuite)

    # Base : structure vs ATR
    sl_atr = float(entry) - float(ATR_MULT_SL) * float(atr_val)
    base = min(float(swing), float(sl_atr))

    # Buffers (pct + ticks)
    raw = base * (1.0 - float(SL_BUFFER_PCT))
    raw = _round_to_tick(raw, float(tick)) - float(SL_BUFFER_TICKS) * float(tick)

    # Clamps & sécurité
    return _apply_common_clamps(entry=float(entry), sl_raw=float(raw), side="buy",
                                tick=float(tick), atr_value=float(atr_val))


def protective_stop_short(df: pd.DataFrame, entry: float, tick: float) -> float:
    """
    SL SHORT “insto”:
      base = max( swing_high, entry + ATR_MULT_SL * ATR )
      puis buffers:
        - pourcentage: base * (1 + SL_BUFFER_PCT)
        - ticks:       + SL_BUFFER_TICKS * tick
      ensuite clamps: MAX_SL_PCT, ATR_MULT_SL_CAP, MIN_SL_TICKS, alignement tick, côté.
    """
    atr_val = _compute_atr(df)

    # Structure (fallback sur high[-2] si rolling indispo)
    swing = _swing_high(df, int(STRUCT_LOOKBACK))
    if swing is None:
        try:
            swing = float(df["high"].iloc[-2])
        except Exception:
            swing = entry  # worst-case fallback

    # Base : structure vs ATR
    sl_atr = float(entry) + float(ATR_MULT_SL) * float(atr_val)
    base = max(float(swing), float(sl_atr))

    # Buffers (pct + ticks)
    raw = base * (1.0 + float(SL_BUFFER_PCT))
    raw = _round_to_tick(raw, float(tick)) + float(SL_BUFFER_TICKS) * float(tick)

    # Clamps & sécurité
    return _apply_common_clamps(entry=float(entry), sl_raw=float(raw), side="sell",
                                tick=float(tick), atr_value=float(atr_val))
