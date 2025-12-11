# =====================================================================
# structure_utils.py — Desk Lead Structure Engine
# Institutional-grade BOS / CHOCH / Liquidity / Trend + HTF alignment
# =====================================================================

from typing import Dict, Any, List, Optional

import numpy as np
import pandas as pd


# =====================================================================
# SWINGS
# =====================================================================

def find_swings(df: pd.DataFrame, left: int = 3, right: int = 3) -> Dict[str, List[tuple]]:
    """
    Détection basique mais robuste des swings (pivot highs / lows).

    On scanne chaque point i et on vérifie :
      - pivot high : high[i] est le max du voisinage [i-left, i+right]
      - pivot low  : low[i]  est le min du voisinage [i-left, i+right]

    Retourne :
      {"highs": [(index, price), ...], "lows": [(index, price), ...]}
    """
    if df is None or len(df) < left + right + 3:
        return {"highs": [], "lows": []}

    highs: List[tuple] = []
    lows: List[tuple] = []

    h = df["high"].values
    l = df["low"].values

    for i in range(left, len(df) - right):
        window_h = h[i - left : i + right + 1]
        window_l = l[i - left : i + right + 1]
        hi = h[i]
        lo = l[i]

        if hi == window_h.max():
            highs.append((int(i), float(hi)))
        if lo == window_l.min():
            lows.append((int(i), float(lo)))

    return {"highs": highs, "lows": lows}


# =====================================================================
# LIQUIDITY LEVELS (Equal Highs / Equal Lows)
# =====================================================================

def _cluster_levels(levels: List[float], tolerance: float) -> List[float]:
    """
    Regroupe des niveaux proches (equal highs / lows).
    tolerance : distance absolue max pour les considérer "égaux".
    """
    if not levels:
        return []

    levels_sorted = sorted(levels)
    clusters: List[List[float]] = [[levels_sorted[0]]]

    for lv in levels_sorted[1:]:
        if abs(lv - clusters[-1][-1]) <= tolerance:
            clusters[-1].append(lv)
        else:
            clusters.append([lv])

    # Retourne la moyenne de chaque cluster avec au moins 2 points
    result = [float(np.mean(c)) for c in clusters if len(c) >= 2]
    return result


def detect_equal_levels(
    df: pd.DataFrame,
    left: int = 3,
    right: int = 3,
    max_window: int = 200,
) -> Dict[str, List[float]]:
    """
    Détecte les equal highs / equal lows à partir des swings.

    - On limite l'analyse aux derniers max_window points pour rester pertinent
      sur le contexte local.
    - La tolérance en prix est basée sur la volatilité récente :
        tolerance ≈ 15% de la médiane des ranges (high-low).

    Retourne :
      {
        "eq_highs": [prix_niveau1, prix_niveau2, ...],
        "eq_lows":  [ ... ]
      }
    """
    if df is None or len(df) < left + right + 3:
        return {"eq_highs": [], "eq_lows": []}

    sub = df.tail(max_window).reset_index(drop=True)
    swings = find_swings(sub, left=left, right=right)

    high_levels = [p for (_, p) in swings["highs"]]
    low_levels = [p for (_, p) in swings["lows"]]

    ranges = sub["high"] - sub["low"]
    if ranges.dropna().empty:
        base_range = np.nan
    else:
        base_range = float(np.nanmedian(ranges))

    if not np.isfinite(base_range) or base_range <= 0:
        # fallback : tolérance fixe
        tolerance = float(sub["close"].iloc[-1]) * 0.001  # 0.1%
    else:
        tolerance = base_range * 0.15

    eq_highs = _cluster_levels(high_levels, tolerance)
    eq_lows = _cluster_levels(low_levels, tolerance)

    return {"eq_highs": eq_highs, "eq_lows": eq_lows}


# =====================================================================
# TREND DETECTION (EMA 20 / 50)
# =====================================================================

def _trend_from_ema(close: pd.Series, fast: int = 20, slow: int = 50) -> str:
    """
    Détecte le biais principal à partir des EMA 20 / 50 :
      - LONG  : ema20 > ema50 et ema20 en pente ascendante
      - SHORT : ema20 < ema50 et ema20 en pente descendante
      - RANGE : sinon
    """
    if close is None or len(close) < slow + 5:
        return "RANGE"

    ema_fast = close.ewm(span=fast, adjust=False).mean()
    ema_slow = close.ewm(span=slow, adjust=False).mean()

    if len(ema_fast) < 5 or len(ema_slow) < 5:
        return "RANGE"

    ef = ema_fast.iloc[-5:]
    es = ema_slow.iloc[-5:]

    slope_fast = ef.iloc[-1] - ef.iloc[0]

    if ef.iloc[-1] > es.iloc[-1] and slope_fast > 0:
        return "LONG"
    if ef.iloc[-1] < es.iloc[-1] and slope_fast < 0:
        return "SHORT"
    return "RANGE"


# =====================================================================
# BOS / CHOCH DETECTION
# =====================================================================

def _detect_bos_or_choch(df: pd.DataFrame) -> Dict[str, Any]:
    """
    Détection simple mais efficace :
      - BOS = break of structure dans la direction du trend EMA.
      - CHOCH = break dans la direction opposée au trend EMA.
      - COS = continuation of structure (BOS aligné avec le trend établi).

    Retourne :
      {
        "bos": bool,
        "choch": bool,
        "cos": bool,
        "direction": "UP" | "DOWN" | None
      }
    """
    if df is None or len(df) < 30:
        return {"bos": False, "choch": False, "cos": False, "direction": None}

    close = df["close"]
    trend = _trend_from_ema(close)

    swings = find_swings(df)
    highs = swings["highs"]
    lows = swings["lows"]

    if len(highs) < 2 and len(lows) < 2:
        return {"bos": False, "choch": False, "cos": False, "direction": None}

    last_close = float(close.iloc[-1])

    bos = False
    choch = False
    cos = False
    direction: Optional[str] = None

    # BOS UP : le prix casse le dernier swing high significatif
    if len(highs) >= 2:
        _, last_hi = highs[-1]
        _, prev_hi = highs[-2]
        level_up = max(last_hi, prev_hi)
        if last_close > level_up:
            bos = True
            direction = "UP"

    # BOS DOWN : le prix casse le dernier swing low significatif
    if not bos and len(lows) >= 2:
        _, last_lo = lows[-1]
        _, prev_lo = lows[-2]
        level_down = min(last_lo, prev_lo)
        if last_close < level_down:
            bos = True
            direction = "DOWN"

    if not bos:
        return {"bos": False, "choch": False, "cos": False, "direction": None}

    # CHOCH vs COS selon accord/désaccord avec le trend EMA
    if direction == "UP":
        if trend == "SHORT":
            choch = True
        else:
            cos = True
    elif direction == "DOWN":
        if trend == "LONG":
            choch = True
        else:
            cos = True

    return {"bos": bos, "choch": choch, "cos": cos, "direction": direction}


# =====================================================================
# HTF TREND ALIGNMENT
# =====================================================================

def htf_trend_ok(df_htf: pd.DataFrame, bias: str) -> bool:
    """
    Vérifie que la tendance H4 (EMA20/EMA50) ne contredit pas le biais H1.

    Règles :
      - Si H4 est LONG, un biais SHORT est rejeté.
      - Si H4 est SHORT, un biais LONG est rejeté.
      - Si H4 est RANGE, on tolère les deux (pas de veto).
    """
    if df_htf is None or len(df_htf) < 60:
        return True  # manque de données => on ne bloque pas

    bias = (bias or "").upper()
    trend_htf = _trend_from_ema(df_htf["close"])

    if trend_htf == "LONG" and bias == "SHORT":
        return False
    if trend_htf == "SHORT" and bias == "LONG":
        return False
    return True


# =====================================================================
# BOS QUALITY (Volume / OI / Liquidity sweep)
# =====================================================================

def bos_quality_details(
    df: pd.DataFrame,
    oi_series: Optional[pd.Series] = None,
    vol_lookback: int = 60,
    vol_pct: float = 0.8,
    oi_min_trend: float = 0.003,
    oi_min_squeeze: float = -0.005,
    df_liq: Optional[pd.DataFrame] = None,
    price: Optional[float] = None,
    tick: float = 0.1,
) -> Dict[str, Any]:
    """
    Evalue la "qualité" d'un BOS récent à partir de :

      - Volume relatif (dernier volume vs moyenne)
      - Corps de bougie (body / range)
      - Variation d'Open Interest autour du move
      - Eventuelle chasse de liquidité (equal highs/lows cassés)

    Retourne un dict contenant :
      {
        "ok": bool,
        "volume_factor": float,
        "body_ratio": float,
        "oi_slope": float,
        "liquidity_sweep": bool,
        "reasons": [ ... ]
      }
    """
    if df is None or len(df) < max(vol_lookback, 20):
        return {"ok": True, "reason": "not_enough_data"}

    closes = df["close"]
    opens = df["open"]
    highs = df["high"]
    lows = df["low"]
    vols = df["volume"]

    last_close = float(closes.iloc[-1])
    last_open = float(opens.iloc[-1])
    last_high = float(highs.iloc[-1])
    last_low = float(lows.iloc[-1])
    last_vol = float(vols.iloc[-1])

    w = df.tail(vol_lookback)
    avg_vol = float(w["volume"].mean()) if not w["volume"].empty else 0.0
    volume_factor = last_vol / avg_vol if avg_vol > 0 else 1.0

    rng = last_high - last_low
    body = abs(last_close - last_open)
    body_ratio = body / rng if rng > 0 else 0.0

    # OI slope sur ~10 dernières barres si dispo
    oi_slope = 0.0
    if oi_series is not None:
        try:
            s = pd.Series(oi_series).astype(float)
            if len(s) >= 10:
                oi_slope = float(s.iloc[-1] - s.iloc[-10]) / max(abs(s.iloc[-10]), 1e-8)
        except Exception:
            oi_slope = 0.0

    # Liquidity sweep : on regarde si un niveau d'égal highs/lows a été dépassé
    liquidity_sweep = False
    if df_liq is not None:
        levels = detect_equal_levels(df_liq.tail(200))
        eq_highs = levels.get("eq_highs", [])
        eq_lows = levels.get("eq_lows", [])

        if price is None:
            price = last_close

        for lvl in eq_highs:
            if last_close > lvl:
                liquidity_sweep = True
                break
        if not liquidity_sweep:
            for lvl in eq_lows:
                if last_close < lvl:
                    liquidity_sweep = True
                    break

    reasons: List[str] = []

    if volume_factor < 1.0 + vol_pct:
        reasons.append("low_volume")

    if body_ratio < 0.35:
        reasons.append("small_body")

    if oi_series is not None and abs(oi_slope) < oi_min_trend:
        reasons.append("weak_oi")

    ok = len(reasons) == 0

    return {
        "ok": ok,
        "volume_factor": float(volume_factor),
        "body_ratio": float(body_ratio),
        "oi_slope": float(oi_slope),
        "liquidity_sweep": bool(liquidity_sweep),
        "reasons": reasons,
    }


# =====================================================================
# STRUCTURE ENGINE (H1)
# =====================================================================

def analyze_structure(df: pd.DataFrame) -> Dict[str, Any]:
    """
    Analyse de structure principale pour H1 :

      - trend : biais EMA20/EMA50 ("LONG" / "SHORT" / "RANGE")
      - swings : dict de pivot highs / lows
      - liquidity : equal highs / equal lows
      - bos / choch / cos : bloc de structure récent
      - oi_series : si une colonne 'oi' est présente dans df
    """
    if df is None or len(df) < 30:
        return {
            "trend": "RANGE",
            "swings": {"highs": [], "lows": []},
            "liquidity": {"eq_highs": [], "eq_lows": []},
            "bos": False,
            "choch": False,
            "cos": False,
            "oi_series": None,
        }

    trend = _trend_from_ema(df["close"])
    swings = find_swings(df)
    levels = detect_equal_levels(df)
    bos_block = _detect_bos_or_choch(df)

    oi_series = df["oi"] if "oi" in df.columns else None

    return {
        "trend": trend,
        "swings": swings,
        "liquidity": levels,
        "bos": bos_block["bos"],
        "cos": bos_block["cos"],
        "choch": bos_block["choch"],
        "oi_series": oi_series,
    }


# =====================================================================
# COMMITMENT SCORE (optionnel)
# =====================================================================

def commitment_score(
    df: pd.DataFrame,
    oi_series: Optional[pd.Series] = None,
    cvd_series: Optional[pd.Series] = None,
) -> float:
    """
    Mesure synthétique de "commitment" insti :
      - Si OI monte + CVD monte → engagement acheteurs
      - Si OI monte + CVD baisse → short build
      - etc.

    Ici on renvoie juste un score normalisé (~[-1, 1]) basé sur
    la dérivée d'oi et de cvd sur ~10 barres.
    """
    try:
        if oi_series is None or cvd_series is None:
            return 0.0

        oi = pd.Series(oi_series).astype(float)
        cvd = pd.Series(cvd_series).astype(float)

        if len(oi) < 10 or len(cvd) < 10:
            return 0.0

        d_oi = oi.iloc[-1] - oi.iloc[-10]
        d_cvd = cvd.iloc[-1] - cvd.iloc[-10]

        score = 0.5 * float(np.tanh(d_oi * 10.0)) + 0.5 * float(np.tanh(d_cvd * 10.0))
        return float(score)
    except Exception:
        return 0.0
