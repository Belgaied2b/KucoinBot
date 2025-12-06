# =====================================================================
# stops.py — Stop-loss institutionnel strict (structure + liquidité + ATR)
# =====================================================================
import pandas as pd
import numpy as np
from typing import Dict, Any, Optional
from .structure_utils import find_swings, detect_equal_levels
from .indicators import true_atr, volatility_regime


def _round_to_tick(x: float, tick: float) -> float:
    if tick <= 0:
        return float(x)
    steps = round(x / tick)
    return round(steps * tick, 12)


def stop_structure(df: pd.DataFrame, bias: str) -> Optional[float]:
    """
    SL basé sur swing structure :
        - LONG : sous le dernier swing low
        - SHORT : au-dessus du dernier swing high
    """
    swings = find_swings(df)
    if bias.upper() == "LONG":
        lows = swings["lows"]
        if not lows:
            return None
        idx = lows[-1]
        return float(df["low"].iloc[idx])

    else:
        highs = swings["highs"]
        if not highs:
            return None
        idx = highs[-1]
        return float(df["high"].iloc[idx])


def stop_liquidity(df: pd.DataFrame, bias: str) -> Optional[float]:
    """
    SL sous les equal lows (LONG) ou au-dessus des equal highs (SHORT).
    """
    liq = detect_equal_levels(df)
    if bias.upper() == "LONG":
        eql = liq["equal_lows"]
        if not eql:
            return None
        # choisir le LOW le plus "proche" (dernier)
        lvl = float(df["low"].iloc[eql[-1]])
        return lvl

    else:
        eqh = liq["equal_highs"]
        if not eqh:
            return None
        lvl = float(df["high"].iloc[eqh[-1]])
        return lvl


def stop_atr(df: pd.DataFrame, bias: str, mult: float = 1.8) -> Optional[float]:
    """
    ATR SL fallback : entry +/- ATR*x
    """
    atr = true_atr(df)
    if len(atr) < 5:
        return None
    a = float(atr.iloc[-1])
    c = float(df["close"].iloc[-1])

    if bias.upper() == "LONG":
        return c - a * mult
    else:
        return c + a * mult


def stop_regime_adjust(sl: float, df: pd.DataFrame, bias: str) -> float:
    """
    Ajuste SL selon régime volatilité :
        HIGH vol → SL élargi
        LOW vol  → SL resserré légèrement
    """
    regime = volatility_regime(df)
    c = float(df["close"].iloc[-1])
    dist = abs(c - sl)

    if regime == "HIGH":
        dist *= 1.25
    elif regime == "LOW":
        dist *= 0.9

    if bias.upper() == "LONG":
        return c - dist
    else:
        return c + dist


def compute_stop_loss(
    df: pd.DataFrame,
    bias: str,
    tick: float = 0.01,
    prefer_liquidity: bool = True
) -> float:
    """
    Stop-loss institutionnel final.
    Ordre de priorité :
        1) Liquidité (equal highs/lows) si dispo
        2) Structure swing
        3) ATR fallback
    Puis :
        + ajustement regime de volatilité
        + arrondi au tick
        + fail-safe global
    """

    bias = bias.upper()
    close = float(df["close"].iloc[-1])

    # ------------------------------
    # 1) Liquidité
    # ------------------------------
    sl_liq = stop_liquidity(df, bias) if prefer_liquidity else None

    # ------------------------------
    # 2) Structure swing
    # ------------------------------
    sl_struct = stop_structure(df, bias)

    # ------------------------------
    # 3) ATR fallback
    # ------------------------------
    sl_atr = stop_atr(df, bias)

    # Choix du SL brut
    candidates = []

    if sl_liq is not None:
        candidates.append(sl_liq)
    if sl_struct is not None:
        candidates.append(sl_struct)
    if sl_atr is not None:
        candidates.append(sl_atr)

    if not candidates:
        # Dernier fail-safe
        if bias == "LONG":
            sl_raw = close * 0.97
        else:
            sl_raw = close * 1.03
    else:
        if bias == "LONG":
            # On veut le SL le plus bas parmi les options
            sl_raw = min(candidates)
        else:
            # On veut le SL le plus haut parmi les options
            sl_raw = max(candidates)

    # ------------------------------
    # Ajustement régime volatilité
    # ------------------------------
    sl_adj = stop_regime_adjust(sl_raw, df, bias)

    # ------------------------------
    # Arrondi au tick
    # ------------------------------
    sl_final = _round_to_tick(sl_adj, tick)

    # ------------------------------
    # Fail-safe : éviter SL trop serré
    # ------------------------------
    dist = abs(close - sl_final)
    if dist < close * 0.0015:  # <0.15%
        if bias == "LONG":
            sl_final = close * 0.9985
        else:
            sl_final = close * 1.0015

    return float(sl_final)
