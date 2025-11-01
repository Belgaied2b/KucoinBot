# tp_clamp.py — TP1 clamp dynamique (volatilité) + garde-fous
from __future__ import annotations
from typing import Optional, Tuple
import math
import pandas as pd

# ===== Settings & fallbacks =====
try:
    from settings import TP1_R_CLAMP_MIN
except Exception:
    TP1_R_CLAMP_MIN = 1.3  # min par défaut si non défini

try:
    from settings import TP1_R_CLAMP_MAX
except Exception:
    TP1_R_CLAMP_MAX = 1.8  # max par défaut si non défini

# Mode “régime de volatilité” (on réutilise les mêmes paramètres que stops.py si dispo)
try:
    from settings import REGIME_MODE
except Exception:
    REGIME_MODE = True

try:
    from settings import REGIME_SOURCE  # "atr" ou "range"
except Exception:
    REGIME_SOURCE = "atr"

try:
    from settings import REGIME_THRESH_LOW
except Exception:
    REGIME_THRESH_LOW = 0.008  # 0.8%
try:
    from settings import REGIME_THRESH_HIGH
except Exception:
    REGIME_THRESH_HIGH = 0.018  # 1.8%

# Multiplicateurs sur le clamp selon le régime (TP1 = prise de profit “rapide” en vol haute)
# High vol → on RACCORCIT le clamp; Low vol → on LARGIT un peu (mouvements plus petits).
REGIME_TP1_CLAMP_MULT = {
    "low": 1.10,     # un peu plus loin
    "normal": 1.00,
    "high": 0.92,    # un peu plus proche
}

# ===== Utils =====
def _round_to_tick(x: float, tick: float) -> float:
    if tick <= 0:
        return float(x)
    steps = round(float(x) / float(tick))
    return round(steps * float(tick), 12)

def _infer_regime(df: Optional[pd.DataFrame]) -> str:
    if not REGIME_MODE or df is None or df.empty:
        return "normal"
    try:
        close = float(df["close"].iloc[-1])
        close = max(close, 1e-12)
        if str(REGIME_SOURCE).lower() == "range":
            hi = float(df["high"].iloc[-1]); lo = float(df["low"].iloc[-1])
            metric = abs(hi - lo) / close
        else:
            # ATR/close simple fallback (vrai ATR non requis pour éviter dépendances)
            hi = df["high"].astype(float)
            lo = df["low"].astype(float)
            cl = df["close"].astype(float)
            prev_c = cl.shift(1)
            tr = pd.concat([(hi - lo).abs(), (hi - prev_c).abs(), (lo - prev_c).abs()], axis=1).max(axis=1)
            atr = float(tr.rolling(14).mean().iloc[-1])
            metric = atr / close if close > 0 else 0.0
    except Exception:
        return "normal"

    if metric <= float(REGIME_THRESH_LOW):
        return "low"
    if metric >= float(REGIME_THRESH_HIGH):
        return "high"
    return "normal"

def _fix_clamp(min_v: float, max_v: float) -> Tuple[float, float]:
    """
    Corrige le cas min==max (bug config) et s’assure min<max.
    """
    try:
        mn = float(min_v)
        mx = float(max_v)
    except Exception:
        mn, mx = 1.3, 1.8
    if not math.isfinite(mn): mn = 1.3
    if not math.isfinite(mx): mx = 1.8
    if mn <= 0: mn = 1.0
    if mx <= 0: mx = 1.5
    if mx <= mn:
        # écarte proprement si mal configuré (ex: 1.5 / 1.5)
        mx = mn + 0.25
    return mn, mx

# ===== Public API =====
def compute_tp1(entry: float,
                sl: float,
                bias: str,
                rr_preferred: Optional[float] = None,
                df: Optional[pd.DataFrame] = None,
                tick: float = 0.01) -> Tuple[float, float]:
    """
    Calcule TP1 avec clamp dynamique:
      - rr_base = rr_preferred (sinon 1.5 par défaut)
      - clamp RR dans [TP1_R_CLAMP_MIN, TP1_R_CLAMP_MAX]
      - ajuste ce clamp selon le régime de volatilité (REGIME_TP1_CLAMP_MULT)
      - retourne (tp1_arrondi, rr_utilisé)
    """
    entry = float(entry); sl = float(sl); tick = float(tick)
    side = str(bias or "LONG").upper()
    rr_base = float(rr_preferred) if (rr_preferred is not None and math.isfinite(rr_preferred) and rr_preferred > 0) else 1.5

    # Clamp de base corrigé
    base_min, base_max = _fix_clamp(TP1_R_CLAMP_MIN, TP1_R_CLAMP_MAX)

    # Régime
    regime = _infer_regime(df)
    mult = float(REGIME_TP1_CLAMP_MULT.get(regime, 1.0))
    rr_min = base_min * mult
    rr_max = base_max * mult
    # sécurité: si inversion par mult
    if rr_max <= rr_min:
        rr_max = rr_min + 0.2

    # Clamp final du RR
    rr_used = max(rr_min, min(rr_base, rr_max))

    # Calcul TP1
    if side == "LONG":
        risk = max(1e-12, entry - sl)
        tp1 = entry + rr_used * risk
        # assure au moins quelques ticks “devant”
        tp1 = max(tp1, entry + 2 * tick)
    else:
        risk = max(1e-12, sl - entry)
        tp1 = entry - rr_used * risk
        tp1 = min(tp1, entry - 2 * tick)

    return _round_to_tick(tp1, tick), float(rr_used)
