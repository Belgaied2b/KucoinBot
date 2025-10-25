# stops.py — stops structure + ATR + buffers
from __future__ import annotations
from typing import Optional
import numpy as np
import pandas as pd

from indicators_true_atr import atr_wilder
from settings import ATR_LEN, ATR_MULT_SL, STRUCT_LOOKBACK, SL_BUFFER_PCT, SL_BUFFER_TICKS


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


def protective_stop_long(df: pd.DataFrame, entry: float, tick: float) -> float:
    """
    SL LONG “insto”:
      base = min( swing_low, entry - ATR_MULT_SL * ATR )
      puis buffer pour couvrir bruit/liquidité:
        - pourcentage: base * (1 - SL_BUFFER_PCT)
        - ticks: - SL_BUFFER_TICKS * tick
      garde-fou: ne jamais remonter au-dessus de (entry - tick)

    NOTE: l'application d'un pourcentage sur le prix absolu suit ta logique d'origine.
    """
    # ATR Wilder (fallback si NaN)
    try:
        atr_series = atr_wilder(df, int(ATR_LEN))
        atr_val = _safe_last(atr_series)
        if atr_val is None or atr_val <= 0:
            atr_val = _fallback_atr(df, int(ATR_LEN))
    except Exception:
        atr_val = _fallback_atr(df, int(ATR_LEN))

    # Structure (fallback sur low[-2] si rolling indispo)
    swing = _swing_low(df, int(STRUCT_LOOKBACK))
    if swing is None:
        try:
            swing = float(df["low"].iloc[-2])
        except Exception:
            swing = entry  # worst-case fallback

    sl_atr = float(entry) - float(ATR_MULT_SL) * float(atr_val)
    base = min(float(swing), float(sl_atr))

    # Buffers (pct + ticks)
    raw = base * (1.0 - float(SL_BUFFER_PCT))
    sl = _round_to_tick(raw, float(tick)) - float(SL_BUFFER_TICKS) * float(tick)

    # Garde-fou: le SL d'un long doit rester < entry
    min_allowed = float(entry) - max(float(tick), 0.0)
    sl = min(sl, min_allowed)

    # Evite valeurs absurdes/négatives
    return max(1e-8, float(sl))


def protective_stop_short(df: pd.DataFrame, entry: float, tick: float) -> float:
    """
    SL SHORT “insto”:
      base = max( swing_high, entry + ATR_MULT_SL * ATR )
      puis buffer pour couvrir bruit/liquidité:
        - pourcentage: base * (1 + SL_BUFFER_PCT)
        - ticks: + SL_BUFFER_TICKS * tick
      garde-fou: ne jamais descendre en-dessous de (entry + tick)
    """
    # ATR Wilder (fallback si NaN)
    try:
        atr_series = atr_wilder(df, int(ATR_LEN))
        atr_val = _safe_last(atr_series)
        if atr_val is None or atr_val <= 0:
            atr_val = _fallback_atr(df, int(ATR_LEN))
    except Exception:
        atr_val = _fallback_atr(df, int(ATR_LEN))

    # Structure
    swing = _swing_high(df, int(STRUCT_LOOKBACK))
    if swing is None:
        try:
            swing = float(df["high"].iloc[-2])
        except Exception:
            swing = entry  # worst-case fallback

    sl_atr = float(entry) + float(ATR_MULT_SL) * float(atr_val)
    base = max(float(swing), float(sl_atr))

    # Buffers (pct + ticks)
    raw = base * (1.0 + float(SL_BUFFER_PCT))
    sl = _round_to_tick(raw, float(tick)) + float(SL_BUFFER_TICKS) * float(tick)

    # Garde-fou: le SL d'un short doit rester > entry
    min_allowed = float(entry) + max(float(tick), 0.0)
    sl = max(sl, min_allowed)

    return max(1e-8, float(sl))
