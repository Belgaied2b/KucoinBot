# stops.py — SL "desk pro": Liquidité (prioritaire) + Structure H1 (limite) > ATR (dernier recours) + logs + régime vol + meta
from __future__ import annotations
from typing import Optional, List, Tuple, Dict, Union
import numpy as np
import pandas as pd
import logging

from indicators_true_atr import atr_wilder
from institutional_data import detect_liquidity_clusters
from settings import (
    ATR_LEN, ATR_MULT_SL, STRUCT_LOOKBACK,
    SL_BUFFER_PCT, SL_BUFFER_TICKS,
)

LOGGER = logging.getLogger(__name__)

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

# Buffers & lookback spécifiques "liquidité"
try:
    from settings import LIQ_LOOKBACK
except Exception:
    LIQ_LOOKBACK = max(80, int(STRUCT_LOOKBACK))  # plus large pour mieux capter

try:
    from settings import LIQ_BUFFER_PCT
except Exception:
    LIQ_BUFFER_PCT = max(0.0, float(SL_BUFFER_PCT))  # par défaut = buffer SL

try:
    from settings import LIQ_BUFFER_TICKS
except Exception:
    LIQ_BUFFER_TICKS = max(3, int(SL_BUFFER_TICKS + 1))  # un peu plus que SL buffer

# Tolérance liquidité adaptative
try:
    from settings import LIQ_TOL_BPS_MIN
except Exception:
    LIQ_TOL_BPS_MIN = 5  # 0.05% mini

try:
    from settings import LIQ_TOL_TICKS
except Exception:
    LIQ_TOL_TICKS = 3  # égalité si écart <= 3 ticks

# --------- Paramètres "régime de volatilité" (fallback si absents) ----------
# Activation du mode régime
try:
    from settings import REGIME_MODE
except Exception:
    REGIME_MODE = True

# Source du régime: "atr" (ATR/close) ou "range" (High-Low)/Close
try:
    from settings import REGIME_SOURCE
except Exception:
    REGIME_SOURCE = "atr"

# Seuils (%) pour déterminer low/normal/high (en proportion, ex. 0.008 = 0.8%)
try:
    from settings import REGIME_THRESH_LOW
except Exception:
    REGIME_THRESH_LOW = 0.008  # 0.8%

try:
    from settings import REGIME_THRESH_HIGH
except Exception:
    REGIME_THRESH_HIGH = 0.018  # 1.8%

# Multipliers/caps par régime (buffers & caps dynamiques)
# PCT/TICKS appliqués *en plus* des valeurs de base
try:
    from settings import REGIME_SL_BUFFER_PCT_MULT
except Exception:
    REGIME_SL_BUFFER_PCT_MULT = {"low": 0.8, "normal": 1.0, "high": 1.25}

try:
    from settings import REGIME_SL_BUFFER_TICKS_ADD
except Exception:
    REGIME_SL_BUFFER_TICKS_ADD = {"low": 0, "normal": 0, "high": 1}

try:
    from settings import REGIME_ATR_MULT_SL_CAP_MULT
except Exception:
    REGIME_ATR_MULT_SL_CAP_MULT = {"low": 0.9, "normal": 1.0, "high": 1.2}

# ============================================================================
# ------------------------- Utils génériques -------------------------
def _round_to_tick(x: float, tick: float) -> float:
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
    try:
        s = df["low"].rolling(int(max(2, lookback))).min()
        v = float(s.iloc[-2])
        return None if np.isnan(v) or np.isinf(v) else v
    except Exception:
        return None

def _swing_high(df: pd.DataFrame, lookback: int) -> Optional[float]:
    try:
        s = df["high"].rolling(int(max(2, lookback))).max()
        v = float(s.iloc[-2])
        return None if np.isnan(v) or np.isinf(v) else v
    except Exception:
        return None

def _compute_atr(df: pd.DataFrame) -> float:
    try:
        atr_series = atr_wilder(df, int(ATR_LEN))
        atr_val = _safe_last(atr_series)
        if atr_val is None or atr_val <= 0:
            atr_val = _fallback_atr(df, int(ATR_LEN))
    except Exception:
        atr_val = _fallback_atr(df, int(ATR_LEN))
    return float(max(0.0, atr_val or 0.0))

def _pct_range_last(df: pd.DataFrame) -> float:
    """(High-Low)/Close sur la dernière bougie disponible, sécurisé."""
    try:
        h = float(df["high"].iloc[-1])
        l = float(df["low"].iloc[-1])
        c = float(df["close"].iloc[-1])
        c = max(c, 1e-12)
        return abs(h - l) / c
    except Exception:
        return 0.0

# ----------------------- Régime de volatilité -----------------------
def _infer_regime(df: pd.DataFrame, atr_val: float) -> str:
    """
    Renvoie 'low' / 'normal' / 'high' selon REGIME_SOURCE et seuils.
    """
    if not REGIME_MODE:
        return "normal"

    metric: float
    if str(REGIME_SOURCE).lower() == "range":
        metric = _pct_range_last(df)
    else:
        # défaut: "atr" → ATR / close
        try:
            c = float(df["close"].iloc[-1])
            c = max(c, 1e-12)
            metric = float(atr_val) / c
        except Exception:
            metric = 0.0

    if metric <= float(REGIME_THRESH_LOW):
        return "low"
    if metric >= float(REGIME_THRESH_HIGH):
        return "high"
    return "normal"

def _regime_adjustments(regime: str) -> Dict[str, float]:
    """
    Retourne les ajustements effectifs pour SL:
    - buffer_pct_mult
    - buffer_ticks_add
    - atr_cap_mult
    """
    r = (regime or "normal").lower()
    return {
        "buffer_pct_mult": float(REGIME_SL_BUFFER_PCT_MULT.get(r, 1.0)),
        "buffer_ticks_add": float(REGIME_SL_BUFFER_TICKS_ADD.get(r, 0)),
        "atr_cap_mult": float(REGIME_ATR_MULT_SL_CAP_MULT.get(r, 1.0)),
    }

# ------------------------- Liquidité (détection) -------------------------
def _adaptive_tol(price: float, tick: float) -> float:
    """
    Tolérance relative pour 'equal highs/lows':
    max( LIQ_TOL_BPS_MIN bps,  (LIQ_TOL_TICKS * tick)/price )
    """
    price = float(max(price, 1e-12))
    tol_from_ticks = (float(LIQ_TOL_TICKS) * float(tick)) / price
    tol_from_bps = float(LIQ_TOL_BPS_MIN) / 1e4
    return max(tol_from_bps, tol_from_ticks)

def _detect_eq_local(df: pd.DataFrame, lookback: int, tol_rel: float) -> Tuple[List[float], List[float]]:
    """
    Détection locale 'equal highs/lows' si institutional_data.detect_liquidity_clusters
    ne retourne rien. On compare bougies successives (ou patterns triples) avec tolérance relative.
    """
    try:
        highs = df["high"].astype(float).tail(lookback).to_numpy()
        lows  = df["low"].astype(float).tail(lookback).to_numpy()
    except Exception:
        return [], []

    eq_highs, eq_lows = set(), set()
    for i in range(1, len(highs)):
        try:
            if abs(highs[i] - highs[i - 1]) / max(1e-12, highs[i]) <= tol_rel:
                eq_highs.add(round(float(highs[i]), 8))
            if abs(lows[i] - lows[i - 1]) / max(1e-12, lows[i]) <= tol_rel:
                eq_lows.add(round(float(lows[i]), 8))
        except Exception:
            continue

    # petit pattern 3-point (H==H==H / L==L==L)
    for i in range(2, len(highs)):
        try:
            h1, h2, h3 = highs[i-2], highs[i-1], highs[i]
            if max(abs(h1-h2), abs(h2-h3), abs(h1-h3)) / max(1e-12, h2) <= tol_rel:
                eq_highs.add(round(float(h2), 8))
            l1, l2, l3 = lows[i-2], lows[i-1], lows[i]
            if max(abs(l1-l2), abs(l2-l3), abs(l1-l3)) / max(1e-12, l2) <= tol_rel:
                eq_lows.add(round(float(l2), 8))
        except Exception:
            continue

    return sorted(eq_highs), sorted(eq_lows)

def _nearest_below(levels: List[float], entry: float) -> Optional[float]:
    below = [float(x) for x in levels if float(x) < float(entry)]
    return max(below) if below else None

def _nearest_above(levels: List[float], entry: float) -> Optional[float]:
    above = [float(x) for x in levels if float(x) > float(entry)]
    return min(above) if above else None

# ------------------------ Garde-fous communs ------------------------
def _apply_common_clamps(entry: float,
                         sl_raw: float,
                         side: str,
                         tick: float,
                         atr_value: float,
                         regime: str,
                         meta: Dict[str, float]) -> float:
    """
    Applique: cap % max, cap ATR (cap dynamique selon régime), distance min ticks, alignement tick, bon côté.
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
            meta["clamp_max_pct"] = max_dist_abs

    # Cap ATR absolu (avec multiplicateur de régime)
    atr_cap_mult = meta.get("atr_cap_mult", 1.0)
    if ATR_MULT_SL_CAP and ATR_MULT_SL_CAP > 0 and atr_value and atr_value > 0:
        atr_cap = float(atr_value) * float(ATR_MULT_SL_CAP) * float(atr_cap_mult)
        if abs(entry - sl) > atr_cap:
            sl = entry - atr_cap if side == "buy" else entry + atr_cap
            meta["clamp_atr_cap"] = atr_cap

    # Distance minimale en ticks
    min_dist = max(float(MIN_SL_TICKS) * tick, tick if tick > 0 else 0.0)
    if abs(entry - sl) < min_dist:
        sl = entry - min_dist if side == "buy" else entry + min_dist
        meta["clamp_min_ticks"] = min_dist

    # Alignement tick
    sl = _round_to_tick(sl, tick)

    # Bon côté après alignement
    if side == "buy":
        sl = min(sl, _round_to_tick(entry - tick, tick))
    else:
        sl = max(sl, _round_to_tick(entry + tick, tick))

    return max(1e-8, float(sl))

# ---------------------- Calculs SL par scénario ----------------------
def _sl_long_from_liquidity(df_liq: pd.DataFrame, entry: float, tick: float,
                            liq_buffer_pct: float, liq_buffer_ticks: float) -> Tuple[Optional[float], Optional[float], str]:
    """Renvoie (sl, lvl, source) pour un long basé sur liquidité; source in {'liquidity','liquidity_local','none'}"""
    # 1) Liquidité via module institutionnel
    try:
        liq = detect_liquidity_clusters(df_liq, lookback=int(LIQ_LOOKBACK), tolerance=0.0005)
        eq_lows = list(liq.get("eq_lows", []))
    except Exception:
        eq_lows = []
    lvl = _nearest_below(eq_lows, float(entry)) if eq_lows else None
    if lvl is not None:
        base = float(lvl) * (1.0 - float(liq_buffer_pct))
        sl = _round_to_tick(base, float(tick)) - float(liq_buffer_ticks) * float(tick)
        return float(sl), float(lvl), "liquidity"

    # 2) Détection locale (encore liquidité, pas structure)
    tol_rel = _adaptive_tol(float(entry), float(tick))
    _eqh, eql = _detect_eq_local(df_liq, lookback=int(LIQ_LOOKBACK), tol_rel=tol_rel)
    lvl = _nearest_below(eql, float(entry)) if eql else None
    if lvl is not None:
        base = float(lvl) * (1.0 - float(liq_buffer_pct))
        sl = _round_to_tick(base, float(tick)) - float(liq_buffer_ticks) * float(tick)
        return float(sl), float(lvl), "liquidity_local"

    return None, None, "none"

def _sl_short_from_liquidity(df_liq: pd.DataFrame, entry: float, tick: float,
                             liq_buffer_pct: float, liq_buffer_ticks: float) -> Tuple[Optional[float], Optional[float], str]:
    """Renvoie (sl, lvl, source) pour un short basé sur liquidité."""
    try:
        liq = detect_liquidity_clusters(df_liq, lookback=int(LIQ_LOOKBACK), tolerance=0.0005)
        eq_highs = list(liq.get("eq_highs", []))
    except Exception:
        eq_highs = []
    lvl = _nearest_above(eq_highs, float(entry)) if eq_highs else None
    if lvl is not None:
        base = float(lvl) * (1.0 + float(liq_buffer_pct))
        sl = _round_to_tick(base, float(tick)) + float(liq_buffer_ticks) * float(tick)
        return float(sl), float(lvl), "liquidity"

    tol_rel = _adaptive_tol(float(entry), float(tick))
    eqh, _eql = _detect_eq_local(df_liq, lookback=int(LIQ_LOOKBACK), tol_rel=tol_rel)
    lvl = _nearest_above(eqh, float(entry)) if eqh else None
    if lvl is not None:
        base = float(lvl) * (1.0 + float(liq_buffer_pct))
        sl = _round_to_tick(base, float(tick)) + float(liq_buffer_ticks) * float(tick)
        return float(sl), float(lvl), "liquidity_local"

    return None, None, "none"

def _swing_buffered_long(df_h1: pd.DataFrame, tick: float, pct_mult: float, ticks_add: float) -> Optional[float]:
    swing = _swing_low(df_h1, int(STRUCT_LOOKBACK))
    if swing is None:
        try:
            swing = float(df_h1["low"].iloc[-2])
        except Exception:
            return None
    base = float(swing) * (1.0 - float(SL_BUFFER_PCT) * float(pct_mult))
    return _round_to_tick(base, float(tick)) - (float(SL_BUFFER_TICKS) + float(ticks_add)) * float(tick)

def _swing_buffered_short(df_h1: pd.DataFrame, tick: float, pct_mult: float, ticks_add: float) -> Optional[float]:
    swing = _swing_high(df_h1, int(STRUCT_LOOKBACK))
    if swing is None:
        try:
            swing = float(df_h1["high"].iloc[-2])
        except Exception:
            return None
    base = float(swing) * (1.0 + float(SL_BUFFER_PCT) * float(pct_mult))
    return _round_to_tick(base, float(tick)) + (float(SL_BUFFER_TICKS) + float(ticks_add)) * float(tick)

# ----------------------------- API publique -----------------------------
# NOTE: df_liq = timeframe dédiée (ex: M15). Si None, on utilise df (H1).

def protective_stop_long(
    df: pd.DataFrame,
    entry: float,
    tick: float,
    df_liq: Optional[pd.DataFrame] = None,
    return_meta: bool = False
) -> Union[float, Tuple[float, Dict[str, float]]]:
    """
    LONG — priorité stricte (hybride):
      1) Liquidité (M15 conseillé via df_liq) la plus proche sous l'entrée (+ buffers liq)
      2) Structure H1: swing low (buffer SL)
      => Fusion: sl_raw = max(sl_liquidity, sl_swing)  # jamais sous le swing H1
      3) ATR: entry - ATR_MULT_SL * ATR   (ultime recours)
      + Garde-fous (cap %, cap ATR, min ticks, alignement, bon côté)
    Retour:
      - Par défaut: float(sl)
      - Si return_meta=True: (sl, meta) où meta contient la source, le niveau, l'ATR, le régime et les clamps.
    """
    atr_val = _compute_atr(df)
    regime = _infer_regime(df, atr_val)
    adj = _regime_adjustments(regime)

    meta: Dict[str, float] = {
        "side": "long",
        "regime": regime,
        "atr": float(atr_val),
        "atr_mult_sl": float(ATR_MULT_SL),
        "buffer_pct_mult": float(adj["buffer_pct_mult"]),
        "buffer_ticks_add": float(adj["buffer_ticks_add"]),
        "atr_cap_mult": float(adj["atr_cap_mult"]),
    }

    base_df_for_liq = df_liq if df_liq is not None else df
    sl_liq, lvl, src = _sl_long_from_liquidity(
        base_df_for_liq, float(entry), float(tick),
        liq_buffer_pct=float(LIQ_BUFFER_PCT) * float(adj["buffer_pct_mult"]),
        liq_buffer_ticks=float(LIQ_BUFFER_TICKS) + float(adj["buffer_ticks_add"]),
    )
    sl_swing = _swing_buffered_long(df, float(tick), pct_mult=float(adj["buffer_pct_mult"]), ticks_add=float(adj["buffer_ticks_add"]))

    if sl_liq is not None and sl_swing is not None:
        raw = max(float(sl_liq), float(sl_swing))  # protège le RR : on ne va pas sous le swing H1
        meta.update({"source": "hybrid", "liquidity_level": float(lvl or 0.0), "swing_level": float(sl_swing)})
        LOGGER.info("[SL] long via hybrid (%s + swing) lvl=%.12f swing_buf=%.12f entry=%.12f -> raw=%.12f | regime=%s",
                    src, float(lvl or 0.0), float(sl_swing), float(entry), float(raw), regime)
    elif sl_liq is not None:
        raw = float(sl_liq)
        meta.update({"source": src, "liquidity_level": float(lvl or 0.0)})
        LOGGER.info("[SL] long via %s lvl=%.12f entry=%.12f -> raw=%.12f | regime=%s",
                    src, float(lvl or 0.0), float(entry), float(raw), regime)
    elif sl_swing is not None:
        raw = float(sl_swing)
        meta.update({"source": "structure", "swing_level": float(sl_swing)})
        LOGGER.info("[SL] long via structure swing_buf=%.12f entry=%.12f -> raw=%.12f | regime=%s",
                    float(sl_swing), float(entry), float(raw), regime)
    else:
        sl_atr = float(entry) - float(ATR_MULT_SL) * float(atr_val)
        base = sl_atr * (1.0 - float(SL_BUFFER_PCT) * float(adj["buffer_pct_mult"]))
        raw = _round_to_tick(base, float(tick)) - (float(SL_BUFFER_TICKS) + float(adj["buffer_ticks_add"])) * float(tick)
        meta.update({"source": "atr"})
        LOGGER.info("[SL] long via atr_fallback atr=%.12f entry=%.12f -> raw=%.12f | regime=%s",
                    float(atr_val), float(entry), float(raw), regime)

    sl = _apply_common_clamps(
        entry=float(entry),
        sl_raw=float(raw),
        side="buy",
        tick=float(tick),
        atr_value=float(atr_val),
        regime=regime,
        meta=meta,
    )
    LOGGER.info("[SL] long final=%.12f (tick=%.12f, regime=%s)", float(sl), float(tick), regime)
    if return_meta:
        meta["sl_final"] = float(sl)
        return float(sl), meta
    return float(sl)

def protective_stop_short(
    df: pd.DataFrame,
    entry: float,
    tick: float,
    df_liq: Optional[pd.DataFrame] = None,
    return_meta: bool = False
) -> Union[float, Tuple[float, Dict[str, float]]]:
    """
    SHORT — priorité stricte (hybride):
      1) Liquidité (M15 conseillé via df_liq) la plus proche au-dessus de l'entrée (+ buffers liq)
      2) Structure H1: swing high (buffer SL)
      => Fusion: sl_raw = min(sl_liquidity, sl_swing)  # jamais au-dessus du swing H1
      3) ATR: entry + ATR_MULT_SL * ATR   (ultime recours)
      + Garde-fous (cap %, cap ATR, min ticks, alignement, bon côté)
    Retour:
      - Par défaut: float(sl)
      - Si return_meta=True: (sl, meta) où meta contient la source, le niveau, l'ATR, le régime et les clamps.
    """
    atr_val = _compute_atr(df)
    regime = _infer_regime(df, atr_val)
    adj = _regime_adjustments(regime)

    meta: Dict[str, float] = {
        "side": "short",
        "regime": regime,
        "atr": float(atr_val),
        "atr_mult_sl": float(ATR_MULT_SL),
        "buffer_pct_mult": float(adj["buffer_pct_mult"]),
        "buffer_ticks_add": float(adj["buffer_ticks_add"]),
        "atr_cap_mult": float(adj["atr_cap_mult"]),
    }

    base_df_for_liq = df_liq if df_liq is not None else df
    sl_liq, lvl, src = _sl_short_from_liquidity(
        base_df_for_liq, float(entry), float(tick),
        liq_buffer_pct=float(LIQ_BUFFER_PCT) * float(adj["buffer_pct_mult"]),
        liq_buffer_ticks=float(LIQ_BUFFER_TICKS) + float(adj["buffer_ticks_add"]),
    )
    sl_swing = _swing_buffered_short(df, float(tick), pct_mult=float(adj["buffer_pct_mult"]), ticks_add=float(adj["buffer_ticks_add"]))

    if sl_liq is not None and sl_swing is not None:
        raw = min(float(sl_liq), float(sl_swing))  # protège le RR : on ne dépasse pas le swing H1
        meta.update({"source": "hybrid", "liquidity_level": float(lvl or 0.0), "swing_level": float(sl_swing)})
        LOGGER.info("[SL] short via hybrid (%s + swing) lvl=%.12f swing_buf=%.12f entry=%.12f -> raw=%.12f | regime=%s",
                    src, float(lvl or 0.0), float(sl_swing), float(entry), float(raw), regime)
    elif sl_liq is not None:
        raw = float(sl_liq)
        meta.update({"source": src, "liquidity_level": float(lvl or 0.0)})
        LOGGER.info("[SL] short via %s lvl=%.12f entry=%.12f -> raw=%.12f | regime=%s",
                    src, float(lvl or 0.0), float(entry), float(raw), regime)
    elif sl_swing is not None:
        raw = float(sl_swing)
        meta.update({"source": "structure", "swing_level": float(sl_swing)})
        LOGGER.info("[SL] short via structure swing_buf=%.12f entry=%.12f -> raw=%.12f | regime=%s",
                    float(sl_swing), float(entry), float(raw), regime)
    else:
        sl_atr = float(entry) + float(ATR_MULT_SL) * float(atr_val)
        base = sl_atr * (1.0 + float(SL_BUFFER_PCT) * float(adj["buffer_pct_mult"]))
        raw = _round_to_tick(base, float(tick)) + (float(SL_BUFFER_TICKS) + float(adj["buffer_ticks_add"])) * float(tick)
        meta.update({"source": "atr"})
        LOGGER.info("[SL] short via atr_fallback atr=%.12f entry=%.12f -> raw=%.12f | regime=%s",
                    float(atr_val), float(entry), float(raw), regime)

    sl = _apply_common_clamps(
        entry=float(entry),
        sl_raw=float(raw),
        side="sell",
        tick=float(tick),
        atr_value=float(atr_val),
        regime=regime,
        meta=meta,
    )
    LOGGER.info("[SL] short final=%.12f (tick=%.12f, regime=%s)", float(sl), float(tick), regime)
    if return_meta:
        meta["sl_final"] = float(sl)
        return float(sl), meta
    return float(sl)
