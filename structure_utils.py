# ============================================================
# structure_utils.py — VERSION FUSION DESK LEAD (ADD-ON)
# ============================================================
# CONTIENT :
#   ✔ Moteur original (swings, pivots, HH/HL/LH/LL)
#   ✔ trend_state, CHoCH, COS, phase
#   ✔ bos_quality, commitment
#   ✔ Engulfing (bullish / bearish)
#   ✔ + AJOUT INSTITUTIONNEL COMPLET (Playbook C)
#       → sweeps institutionnels
#       → FVG institutionnels
#       → discount/premium zones
#       → age_since_bos
#       → setup_type: inst_pullback / inst_continuation / inst_sweep_reversal
#
#   → 100% rétro-compatible avec analyze_signal.py et scanner.py
# ============================================================

from __future__ import annotations

import numpy as np
import pandas as pd
from typing import Optional, List, Dict, Any


# =====================================================================
# PIVOTS & SWINGS
# =====================================================================

def _detect_pivots(
    df: pd.DataFrame,
    left: int = 2,
    right: int = 2,
    max_bars: int = 300
) -> List[Dict[str, Any]]:
    """
    Détection brute des pivots high/low avec fenêtre (left/right).
    """
    n = len(df)
    if n < left + right + 3:
        return []

    try:
        highs = df["high"].astype(float).to_numpy()
        lows = df["low"].astype(float).to_numpy()
    except Exception:
        return []

    start = max(left, n - max_bars)
    end = n - right
    pivots: List[Dict[str, Any]] = []

    for i in range(start, end):
        window_h = highs[i - left: i + right + 1]
        window_l = lows[i - left: i + right + 1]
        h = highs[i]
        l = lows[i]

        if h == float(window_h.max()):
            pivots.append({"pos": i, "kind": "high", "price": float(h)})
        if l == float(window_l.min()):
            pivots.append({"pos": i, "kind": "low", "price": float(l)})

    if not pivots:
        return []

    pivots.sort(key=lambda p: p["pos"])

    # compressions (on garde le plus extrême par type)
    compressed: List[Dict[str, Any]] = []
    for p in pivots:
        if not compressed:
            compressed.append(p)
            continue
        last = compressed[-1]
        if p["kind"] != last["kind"]:
            compressed.append(p)
        else:
            if p["kind"] == "high":
                if p["price"] >= last["price"]:
                    compressed[-1] = p
            else:
                if p["price"] <= last["price"]:
                    compressed[-1] = p

    return compressed[-max_bars:]


def _build_swings(
    df: pd.DataFrame,
    left: int = 2,
    right: int = 2,
    max_pivots: int = 50
) -> List[Dict[str, Any]]:
    """
    Construit les swings HH/HL/LH/LL à partir des pivots.
    """
    pivots = _detect_pivots(df, left=left, right=right, max_bars=max_pivots * 3)
    if not pivots:
        return []

    swings: List[Dict[str, Any]] = []
    last_high: Optional[float] = None
    last_low: Optional[float] = None

    for p in pivots[-max_pivots:]:
        label = None
        if p["kind"] == "high":
            if last_high is None:
                label = "H"
            else:
                label = "HH" if p["price"] > last_high else "LH"
            last_high = p["price"]
        else:
            if last_low is None:
                label = "L"
            else:
                label = "HL" if p["price"] > last_low else "LL"
            last_low = p["price"]

        swings.append(
            {
                "pos": int(p["pos"]),
                "kind": p["kind"],
                "price": float(p["price"]),
                "label": label,
            }
        )

    return swings


def _trend_from_labels(labels: List[str]) -> str:
    """
    Déduit la tendance globale à partir des labels HH/HL/LH/LL.
    """
    if not labels:
        return "unknown"

    from collections import Counter

    c = Counter([x for x in labels if x])
    hh = c.get("HH", 0)
    hl = c.get("HL", 0)
    lh = c.get("LH", 0)
    ll = c.get("LL", 0)

    up_score = hh + hl
    down_score = ll + lh

    if up_score >= 2 and ll == 0 and up_score >= down_score:
        return "up"
    if down_score >= 2 and hh == 0 and down_score >= up_score:
        return "down"
    if up_score == 0 and down_score == 0:
        return "unknown"
    return "range"


# =====================================================================
# ANALYZE STRUCTURE (BOS / CHoCH / COS / phase)
# =====================================================================

def analyze_structure(
    df: pd.DataFrame,
    bias: Optional[str] = None,
    left: int = 2,
    right: int = 2,
    max_pivots: int = 50
) -> Dict[str, Any]:
    """
    Retourne :
      - swings (HH/HL/LH/LL)
      - bos_direction (UP/DOWN)
      - choch_direction
      - trend_state (up/down/range/unknown)
      - phase (expansion/pullback/accumulation/distribution)
      - cos (range_to_trend, trend_to_range)
      - last_event
      - + add-on institutionnel (voir _institutional_addon)
    """
    out: Dict[str, Any] = {
        "swings": [],
        "bos_direction": None,
        "choch_direction": None,
        "trend_state": "unknown",
        "phase": "unknown",
        "cos": None,
        "last_event": None,
    }

    if df is None or len(df) < 10:
        return out

    swings = _build_swings(df, left=left, right=right, max_pivots=max_pivots)
    out["swings"] = swings
    if not swings:
        return out

    close = float(df["close"].iloc[-1])

    # Dernier swing high/low utile
    last_high = None
    last_low = None
    for s in swings:
        if s["pos"] >= len(df) - 1:
            continue
        if s["kind"] == "high":
            if last_high is None or s["pos"] >= last_high["pos"]:
                last_high = s
        else:
            if last_low is None or s["pos"] >= last_low["pos"]:
                last_low = s

    bos_dir = None
    if last_high is not None and close > float(last_high["price"]):
        bos_dir = "UP"
    elif last_low is not None and close < float(last_low["price"]):
        bos_dir = "DOWN"
    out["bos_direction"] = bos_dir

    labels = [s.get("label") for s in swings if s.get("label")]
    trend = _trend_from_labels(labels)
    out["trend_state"] = trend

    prev_trend = _trend_from_labels(labels[:-2]) if len(labels) >= 4 else "unknown"

    choch = None
    cos = None
    last_event = None

    if prev_trend in ("up", "down") and trend in ("up", "down") and prev_trend != trend:
        choch = "UP" if trend == "up" else "DOWN"
        last_event = f"choch_{trend}"
    elif prev_trend in ("up", "down") and trend == "range":
        cos = "trend_to_range"
        last_event = "cos_trend_to_range"
    elif prev_trend == "range" and trend in ("up", "down"):
        cos = "range_to_trend"
        last_event = "cos_range_to_trend"
    elif bos_dir is not None:
        last_event = f"bos_{bos_dir.lower()}"

    out["choch_direction"] = choch
    out["cos"] = cos
    out["last_event"] = last_event

    # Phase
    if trend == "up":
        out["phase"] = "expansion" if bos_dir == "UP" else "pullback"
    elif trend == "down":
        out["phase"] = "expansion" if bos_dir == "DOWN" else "pullback"
    elif trend == "range":
        if prev_trend == "up":
            out["phase"] = "distribution"
        elif prev_trend == "down":
            out["phase"] = "accumulation"

    # -----------------------------------------------------------
    # ADD-ON INSTITUTIONNEL (Playbook C)
    # -----------------------------------------------------------
    inst = _institutional_addon(df, out, bias)
    out.update(inst)

    return out


# =====================================================================
# structure_valid + detect_bos (API legacy)
# =====================================================================

def detect_bos(df: pd.DataFrame, lookback: int = 10):
    """
    Legacy simple : renvoie 'BOS_UP' / 'BOS_DOWN' / None.
    """
    ctx = analyze_structure(df)
    if ctx.get("bos_direction") == "UP":
        return "BOS_UP"
    if ctx.get("bos_direction") == "DOWN":
        return "BOS_DOWN"
    return None


def structure_valid(df: pd.DataFrame, bias: str, lookback: int = 10) -> bool:
    """
    Condition legacy utilisée par analyze_signal.
    OK si BOS/trend aligné avec le biais.
    """
    if df is None or len(df) < max(5, lookback):
        return True

    ctx = analyze_structure(df, bias)
    bos = ctx.get("bos_direction")
    trend = ctx.get("trend_state")

    b = str(bias or "").upper()
    if b == "LONG":
        return bool(bos == "UP" or trend == "up")
    if b == "SHORT":
        return bool(bos == "DOWN" or trend == "down")
    return True


# =====================================================================
# HTF Trend — EMA 20/50
# =====================================================================

def _ema(x: pd.Series, n: int = 20) -> pd.Series:
    return x.ewm(span=n, adjust=False).mean()


def htf_trend_ok(df_htf: Optional[pd.DataFrame], bias: str) -> bool:
    """
    Filtre HTF : close vs EMA20/50.
    """
    if df_htf is None or len(df_htf) < 60:
        return True
    close = df_htf["close"].astype(float)
    ema20 = _ema(close, 20)
    ema50 = _ema(close, 50)
    if str(bias).upper() == "LONG":
        return bool(close.iloc[-1] > ema50.iloc[-1] and ema20.iloc[-1] > ema50.iloc[-1])
    return bool(close.iloc[-1] < ema50.iloc[-1] and ema20.iloc[-1] < ema50.iloc[-1])


# =====================================================================
# bos_quality_details + commitment_score
# =====================================================================

try:
    from institutional_data import detect_liquidity_clusters
except Exception:
    detect_liquidity_clusters = None  # type: ignore[misc]


def bos_quality_details(
    df: pd.DataFrame,
    oi_series: Optional[pd.Series] = None,
    vol_lookback: int = 60,
    vol_pct: float = 0.80,
    oi_min_trend: float = 0.003,
    oi_min_squeeze: float = -0.005,
    df_liq: Optional[pd.DataFrame] = None,
    price: Optional[float] = None,
    tick: float = 0.0,
) -> Dict[str, Any]:
    """
    Qualité d’un BOS :
      - volume
      - variation OI
      - proximité d’une zone de liquidité (equal highs/lows)
    """
    out: Dict[str, Any] = {
        "ok": True,
        "vol_ok": True,
        "oi_ok": True,
        "bos_direction": None,
        "has_liquidity_zone": False,
        "liquidity_side": None,
        "liq_distance": None,
        "liq_distance_bps": None,
    }

    if df is None or len(df) < max(5, vol_lookback):
        return out

    ctx = analyze_structure(df)
    out["bos_direction"] = ctx.get("bos_direction")

    # Volume
    try:
        vol = df["volume"].astype(float).tail(vol_lookback)
        v_last = float(vol.iloc[-1])
        thresh = float(vol.quantile(vol_pct))
        vol_ok = v_last >= thresh
    except Exception:
        vol_ok = True
    out["vol_ok"] = vol_ok

    # OI
    oi_ok = True
    if oi_series is not None and len(oi_series) >= 3:
        try:
            o = oi_series.astype(float).tail(3)
            pct = (o.iloc[-1] - o.iloc[0]) / max(1e-12, o.iloc[0])
            oi_ok = (pct >= oi_min_trend) or (pct <= oi_min_squeeze)
        except Exception:
            oi_ok = True
    out["oi_ok"] = oi_ok
    out["ok"] = bool(vol_ok and oi_ok)

    # Liquidity
    ref_price = float(price) if price is not None else float(df["close"].iloc[-1])

    eq_highs: List[float] = []
    eq_lows: List[float] = []

    try:
        base_df = df_liq if isinstance(df_liq, pd.DataFrame) else df
        if detect_liquidity_clusters is not None:
            liq = detect_liquidity_clusters(base_df, lookback=80, tolerance=0.0005)
            if isinstance(liq, dict):
                eq_highs = liq.get("eq_highs", [])
                eq_lows = liq.get("eq_lows", [])
    except Exception:
        pass

    if eq_highs or eq_lows:
        out["has_liquidity_zone"] = True
        all_lvls = (
            [(abs(h - ref_price), h, "UP") for h in eq_highs]
            + [(abs(l - ref_price), l, "DOWN") for l in eq_lows]
        )
        all_lvls.sort(key=lambda x: x[0])
        if all_lvls:
            dist, lvl, side = all_lvls[0]
            out["liquidity_side"] = side
            out["liq_distance"] = float(dist)
            out["liq_distance_bps"] = float((dist / max(1e-12, ref_price)) * 10000)

    return out


def bos_quality_ok(
    df: pd.DataFrame,
    oi_series=None,
    vol_lookback=60,
    vol_pct=0.80,
    oi_min_trend=0.003,
    oi_min_squeeze=-0.005,
):
    return bool(
        bos_quality_details(
            df=df,
            oi_series=oi_series,
            vol_lookback=vol_lookback,
            vol_pct=vol_pct,
            oi_min_trend=oi_min_trend,
            oi_min_squeeze=oi_min_squeeze,
        ).get("ok", True)
    )


def commitment_score(oi_series, cvd_series, lookback: int = 80) -> float:
    """
    Score 0..1 de "commitment" des gros flux (OI + CVD).
    """
    try:
        if oi_series is None or cvd_series is None:
            return 0.5
        oi = pd.Series(oi_series).dropna().tail(lookback)
        cvd = pd.Series(cvd_series).dropna().tail(lookback)
    except Exception:
        return 0.5

    if len(oi) < 5 or len(cvd) < 5:
        return 0.5

    try:
        oi_delta = oi.iloc[-1] - oi.iloc[0]
        cvd_delta = cvd.iloc[-1] - cvd.iloc[0]
    except Exception:
        return 0.5

    oi_norm = float(np.clip(oi_delta / max(abs(oi.iloc[0]), 1e-6), -3, 3))
    cvd_norm = float(np.clip(cvd_delta / max(abs(cvd.iloc[0]), 1e-6), -3, 3))

    mag = 0.5 * (abs(oi_norm) + abs(cvd_norm))
    mag_score = float(1 - np.exp(-mag))
    same_sign = (oi_norm * cvd_norm) > 0

    align = mag_score if same_sign else -mag_score
    commit = float(np.clip(0.5 + 0.4 * align, 0.0, 1.0))
    return commit


# =====================================================================
# Engulfing Patterns (API legacy)
# =====================================================================

def is_bullish_engulfing(df: pd.DataFrame, lookback: int = 3) -> bool:
    """
    Détection simple bullish engulfing sur la dernière bougie.
    """
    if df is None or len(df) < 2:
        return False

    o1, c1 = float(df["open"].iloc[-2]), float(df["close"].iloc[-2])
    o2, c2 = float(df["open"].iloc[-1]), float(df["close"].iloc[-1])

    # candle 1: rouge, candle 2: verte
    if c1 >= o1 or c2 <= o2:
        return False

    # body 2 englobe body 1
    body1_low = min(o1, c1)
    body1_high = max(o1, c1)
    body2_low = min(o2, c2)
    body2_high = max(o2, c2)

    return body2_low <= body1_low and body2_high >= body1_high


def is_bearish_engulfing(df: pd.DataFrame, lookback: int = 3) -> bool:
    """
    Détection simple bearish engulfing sur la dernière bougie.
    """
    if df is None or len(df) < 2:
        return False

    o1, c1 = float(df["open"].iloc[-2]), float(df["close"].iloc[-2])
    o2, c2 = float(df["open"].iloc[-1]), float(df["close"].iloc[-1])

    # candle 1: verte, candle 2: rouge
    if c1 <= o1 or c2 >= o2:
        return False

    body1_low = min(o1, c1)
    body1_high = max(o1, c1)
    body2_low = min(o2, c2)
    body2_high = max(o2, c2)

    return body2_low <= body1_low and body2_high >= body1_high


# =====================================================================
# ADD-ON INSTITUTIONNEL — Sweeps, FVG, Discount, Setup Type
# =====================================================================

def _equal(a: float, b: float, tol: float = 0.0003) -> bool:
    return abs(a - b) <= tol * max(a, b, 1e-8)


def _inst_sweeps(df: pd.DataFrame) -> Dict[str, bool]:
    if len(df) < 5:
        return {"sweep_high": False, "sweep_low": False}

    h1, h2 = df["high"].iloc[-1], df["high"].iloc[-2]
    l1, l2 = df["low"].iloc[-1], df["low"].iloc[-2]

    eq_high = _equal(h2, df["high"].iloc[-3])
    eq_low = _equal(l2, df["low"].iloc[-3])

    sweep_high = eq_high and (h1 > h2)
    sweep_low = eq_low and (l1 < l2)

    return {"sweep_high": sweep_high, "sweep_low": sweep_low}


def _inst_fvg(df: pd.DataFrame) -> Dict[str, bool]:
    n = len(df)
    up = False
    down = False
    for i in range(max(3, n - 30), n - 1):
        if df["low"].iloc[i] > df["high"].iloc[i - 2]:
            up = True
        if df["high"].iloc[i] < df["low"].iloc[i - 2]:
            down = True
    return {"up_fvg": up, "down_fvg": down}


def _discount_premium(df: pd.DataFrame, bias: str, lookback: int = 50):
    sub = df.tail(lookback)
    hi = float(sub["high"].max())
    lo = float(sub["low"].min())
    mid = 0.5 * (hi + lo)
    close = float(df["close"].iloc[-1])

    if bias.upper() == "LONG":
        return close < mid, False
    else:
        return False, close > mid


def _age_since_last_bos(df: pd.DataFrame, swings: List[Dict[str, Any]]) -> Optional[int]:
    if not swings:
        return None

    close = float(df["close"].iloc[-1])
    last_bos_pos: Optional[int] = None

    for s in swings[::-1]:
        if s["kind"] == "high" and close > s["price"]:
            last_bos_pos = s["pos"]
            break
        if s["kind"] == "low" and close < s["price"]:
            last_bos_pos = s["pos"]
            break

    if last_bos_pos is None:
        return None

    return max(0, len(df) - 1 - last_bos_pos)


def _classify_institutional_setup(
    bias: str,
    sweeps: Dict[str, bool],
    fvg: Dict[str, bool],
    bos_dir: Optional[str],
    discount: bool,
    premium: bool,
    age_bos: Optional[int],
) -> str:
    side = bias.upper()

    # Sweep reversal
    if side == "LONG" and sweeps["sweep_low"]:
        return "inst_sweep_reversal"
    if side == "SHORT" and sweeps["sweep_high"]:
        return "inst_sweep_reversal"

    # Institutional Pullback
    if side == "LONG":
        if bos_dir == "UP" and discount and fvg["up_fvg"]:
            return "inst_pullback"
    else:
        if bos_dir == "DOWN" and premium and fvg["down_fvg"]:
            return "inst_pullback"

    # Institutional Continuation
    if age_bos is not None and age_bos <= 4:
        if side == "LONG" and fvg["up_fvg"] and bos_dir == "UP":
            return "inst_continuation"
        if side == "SHORT" and fvg["down_fvg"] and bos_dir == "DOWN":
            return "inst_continuation"

    return "none"


# =====================================================================
# MODULE ADD-ON — Appliqué dans analyze_structure()
# =====================================================================

def _institutional_addon(
    df: pd.DataFrame,
    base_ctx: Dict[str, Any],
    bias: Optional[str],
) -> Dict[str, Any]:
    """
    Ajoute les métadonnées institutionnelles AU-DESSUS
    sans jamais casser le moteur original.
    """
    sweeps = _inst_sweeps(df)
    fvg = _inst_fvg(df)

    discount, premium = False, False
    if bias:
        try:
            discount, premium = _discount_premium(df, bias)
        except Exception:
            pass

    age = None
    try:
        age = _age_since_last_bos(df, base_ctx.get("swings", []))
    except Exception:
        pass

    setup = None
    if bias:
        setup = _classify_institutional_setup(
            bias=bias,
            sweeps=sweeps,
            fvg=fvg,
            bos_dir=base_ctx.get("bos_direction"),
            discount=discount,
            premium=premium,
            age_bos=age,
        )

    return {
        "sweep_high": sweeps["sweep_high"],
        "sweep_low": sweeps["sweep_low"],
        "up_fvg": fvg["up_fvg"],
        "down_fvg": fvg["down_fvg"],
        "discount": discount,
        "premium": premium,
        "age_since_bos": age,
        "setup_type": setup or "none",
    }
