# ============================================================
# structure_utils.py — VERSION FUSION DESK LEAD (INSTITUTIONNEL V2)
# ============================================================
# CONTIENT :
#   ✔ Moteur original (swings, pivots, HH/HL/LH/LL)
#   ✔ trend_state, CHoCH, COS, phase
#   ✔ bos_quality, commitment
#   ✔ Engulfing (bullish / bearish)
#   ✔ ADD-ON INSTITUTIONNEL COMPLET (Playbook C)
#       → sweeps institutionnels (tolérance dynamique)
#       → FVG institutionnels
#       → discount/premium zones v2
#       → BOS institutionnel (OI/CVD/vol/liquidité)
#       → CHoCH institutionnel
#       → age_since_bos amélioré
#       → setup_type: inst_pullback / inst_continuation / inst_sweep_reversal
#
# → 100% rétro-compatible avec analyze_signal.py et scanner.py
# ============================================================

from __future__ import annotations

import numpy as np
import pandas as pd
from typing import Optional, List, Dict, Any

# ====================================================================================
# UTILITAIRES INSTITUTIONNELS
# ====================================================================================

def _dynamic_tol(price: float, tick: float, bps_min: float = 4, ticks_min: int = 2) -> float:
    """
    Tolérance relative pour equal highs/lows selon :
      - ticks
      - bps
    """
    price = float(max(price, 1e-12))
    tol_ticks = (ticks_min * tick) / price if tick > 0 else 0
    tol_bps = bps_min / 1e4
    return max(tol_ticks, tol_bps)


def _equals_dynamic(a: float, b: float, price: float, tick: float) -> bool:
    tol = _dynamic_tol(price, tick)
    return abs(a - b) <= tol * max(price, 1e-12)


def _detect_equal_highs_lows(df: pd.DataFrame, lookback: int = 50, tick: float = 0.0) -> Dict[str, List[float]]:
    """
    Détection améliorée des equal highs/lows avec tolérance dynamique.
    """
    highs = df["high"].astype(float).tail(lookback).to_numpy()
    lows = df["low"].astype(float).tail(lookback).to_numpy()
    close = float(df["close"].iloc[-1])

    eq_high = set()
    eq_low = set()

    for i in range(1, len(highs)):
        if _equals_dynamic(highs[i], highs[i-1], close, tick):
            eq_high.add(round(highs[i], 8))

        if _equals_dynamic(lows[i], lows[i-1], close, tick):
            eq_low.add(round(lows[i], 8))

    return {
        "eq_highs": sorted(eq_high),
        "eq_lows": sorted(eq_low),
    }

# =====================================================================
# PIVOTS & SWINGS
# =====================================================================

def _detect_pivots(
    df: pd.DataFrame,
    left: int = 2,
    right: int = 2,
    max_bars: int = 300
) -> List[Dict[str, Any]]:
    n = len(df)
    if n < left + right + 3:
        return []

    highs = df["high"].astype(float).to_numpy()
    lows = df["low"].astype(float).to_numpy()

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

    # compression
    compressed: List[Dict[str, Any]] = []
    for p in pivots:
        if not compressed:
            compressed.append(p)
            continue
        last = compressed[-1]
        if p["kind"] != last["kind"]:
            compressed.append(p)
        else:
            if p["kind"] == "high" and p["price"] >= last["price"]:
                compressed[-1] = p
            elif p["kind"] == "low" and p["price"] <= last["price"]:
                compressed[-1] = p

    return compressed[-max_bars:]


def _build_swings(
    df: pd.DataFrame,
    left: int = 2,
    right: int = 2,
    max_pivots: int = 50
) -> List[Dict[str, Any]]:

    pivots = _detect_pivots(df, left=left, right=right, max_bars=max_pivots * 3)
    if not pivots:
        return []

    swings: List[Dict[str, Any]] = []
    last_high = None
    last_low = None

    for p in pivots[-max_pivots:]:
        label = None
        if p["kind"] == "high":
            label = "HH" if (last_high is not None and p["price"] > last_high) else "LH"
            if last_high is None:
                label = "H"
            last_high = p["price"]
        else:
            label = "HL" if (last_low is not None and p["price"] > last_low) else "LL"
            if last_low is None:
                label = "L"
            last_low = p["price"]

        swings.append({
            "pos": int(p["pos"]),
            "kind": p["kind"],
            "price": float(p["price"]),
            "label": label,
        })

    return swings


def _trend_from_labels(labels: List[str]) -> str:
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
# BOS / CHoCH / COS ANALYSIS
# =====================================================================

def analyze_structure(
    df: pd.DataFrame,
    bias: Optional[str] = None,
    left: int = 2,
    right: int = 2,
    max_pivots: int = 50,
    tick: float = 0.0,
    oi_series: Optional[pd.Series] = None,
    cvd_series: Optional[pd.Series] = None,
) -> Dict[str, Any]:

    out = {
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

    # last swing high/low
    last_high = last_low = None
    for s in swings:
        if s["pos"] >= len(df) - 1:
            continue
        if s["kind"] == "high":
            if last_high is None or s["pos"] > last_high["pos"]:
                last_high = s
        else:
            if last_low is None or s["pos"] > last_low["pos"]:
                last_low = s

    # ------------------------
    # BOS DÉTECTION
    # ------------------------
    bos_dir = None
    if last_high and close > last_high["price"]:
        bos_dir = "UP"
    elif last_low and close < last_low["price"]:
        bos_dir = "DOWN"

    # Confirmation institutionnelle du BOS
    bos_inst = institutional_bos_confirmation(
        bos_dir, df, oi_series, cvd_series, tick=tick
    )

    if bos_inst["confirmed"]:
        bos_dir = bos_inst["direction"]

    out["bos_direction"] = bos_dir

    # ------------------------
    # Trend
    # ------------------------
    labels = [s["label"] for s in swings if s.get("label")]
    trend = _trend_from_labels(labels)
    out["trend_state"] = trend

    prev_trend = _trend_from_labels(labels[:-2]) if len(labels) >= 4 else "unknown"

    choch = None
    cos = None
    last_event = None

    # CHoCH institutionnel : trend inverse + BOS opposé précédent
    if bose_opposite := check_institutional_choch(trend, prev_trend, bos_inst):
        choch = bose_opposite
        last_event = f"choch_{trend}"

    # COS
    elif prev_trend in ("up", "down") and trend == "range":
        cos = "trend_to_range"
        last_event = "cos_trend_to_range"
    elif prev_trend == "range" and trend in ("up", "down"):
        cos = "range_to_trend"
        last_event = "cos_range_to_trend"

    elif bos_dir:
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

    # ------------------------
    # ADD-ON INSTITUTIONNEL
    # ------------------------
    inst = _institutional_addon(
        df=df,
        base_ctx=out,
        bias=bias,
        tick=tick,
        oi_series=oi_series,
        cvd_series=cvd_series,
    )
    out.update(inst)

    return out

# =====================================================================
# BOS INSTITUTIONNEL (Volume / OI / CVD / Liquidité)
# =====================================================================

def institutional_bos_confirmation(
    bos_direction: Optional[str],
    df: pd.DataFrame,
    oi_series: Optional[pd.Series],
    cvd_series: Optional[pd.Series],
    tick: float = 0.0,
) -> Dict[str, Any]:

    if bos_direction not in ("UP", "DOWN"):
        return {"confirmed": False, "direction": None}

    vol = df["volume"].astype(float)
    vol_ok = vol.iloc[-1] >= vol.rolling(30).mean().iloc[-1]

    oi_ok = True
    if oi_series is not None and len(oi_series) >= 5:
        o = oi_series.astype(float).tail(5)
        oi_ok = (o.iloc[-1] - o.iloc[0]) >= 0

    cvd_ok = True
    if cvd_series is not None and len(cvd_series) >= 5:
        c = cvd_series.astype(float).tail(5)
        cvd_ok = (c.iloc[-1] - c.iloc[0]) >= 0 if bos_direction == "UP" else (c.iloc[-1] - c.iloc[0]) <= 0

    liq = _detect_equal_highs_lows(df, 60, tick=tick)
    near_liq = False

    if bos_direction == "UP" and liq["eq_highs"]:
        near_liq = float(df["close"].iloc[-1]) >= min(liq["eq_highs"])
    if bos_direction == "DOWN" and liq["eq_lows"]:
        near_liq = float(df["close"].iloc[-1]) <= max(liq["eq_lows"])

    confirmed = bool(vol_ok and oi_ok and cvd_ok)
    return {
        "confirmed": confirmed,
        "direction": bos_direction if confirmed else None,
        "vol_ok": vol_ok,
        "oi_ok": oi_ok,
        "cvd_ok": cvd_ok,
        "near_liq": near_liq,
    }

# =====================================================================
# CHoCH INSTITUTIONNEL
# =====================================================================

def check_institutional_choch(new_trend: str, prev_trend: str, bos_inst: Dict[str, Any]) -> Optional[str]:
    """
    Un CHoCH institutionnel doit :
      - casser un swing dans l'autre sens
      - être aligné avec un BOS inst validé
    """
    if bos_inst["confirmed"] is None:
        return None

    if prev_trend in ("up", "down") and new_trend in ("up", "down"):
        if prev_trend != new_trend:
            return "UP" if new_trend == "up" else "DOWN"

    return None

# =====================================================================
# bos_quality_details et commitment_score (améliorés)
# =====================================================================

try:
    from institutional_data import detect_liquidity_clusters
except Exception:
    detect_liquidity_clusters = None


def bos_quality_details(
    df: pd.DataFrame,
    oi_series: Optional[pd.Series] = None,
    cvd_series: Optional[pd.Series] = None,
    vol_lookback: int = 60,
    vol_pct: float = 0.80,
    df_liq: Optional[pd.DataFrame] = None,
    tick: float = 0.0,
) -> Dict[str, Any]:

    out = {
        "ok": True,
        "vol_ok": True,
        "oi_ok": True,
        "cvd_ok": True,
        "liq_ok": True,
        "bos_direction": None,
    }

    if df is None or len(df) < vol_lookback:
        return out

    ctx = analyze_structure(df)
    out["bos_direction"] = ctx.get("bos_direction")

    # Volume
    vol = df["volume"].astype(float).tail(vol_lookback)
    out["vol_ok"] = vol.iloc[-1] >= vol.quantile(vol_pct)

    # OI confirmation
    if oi_series is not None and len(oi_series) >= 5:
        o = oi_series.astype(float).tail(5)
        out["oi_ok"] = (o.iloc[-1] - o.iloc[0]) >= 0

    # CVD confirmation
    if cvd_series is not None and len(cvd_series) >= 5:
        c = cvd_series.astype(float).tail(5)
        out["cvd_ok"] = (c.iloc[-1] - c.iloc[0]) >= 0 if ctx["bos_direction"] == "UP" else (c.iloc[-1] - c.iloc[0]) <= 0

    out["ok"] = bool(out["vol_ok"] and out["oi_ok"] and out["cvd_ok"])
    return out


def commitment_score(oi_series, cvd_series, lookback: int = 80) -> float:
    if oi_series is None or cvd_series is None:
        return 0.5
    try:
        oi = pd.Series(oi_series).dropna().tail(lookback)
        cvd = pd.Series(cvd_series).dropna().tail(lookback)
    except Exception:
        return 0.5

    if len(oi) < 5 or len(cvd) < 5:
        return 0.5

    oi_delta = oi.iloc[-1] - oi.iloc[0]
    cvd_delta = cvd.iloc[-1] - cvd.iloc[0]

    same = (oi_delta * cvd_delta) > 0
    mag = np.clip(abs(oi_delta) + abs(cvd_delta), 0, 3)

    score = 0.5 + 0.4 * ((mag / 3) if same else -(mag / 3))
    return float(np.clip(score, 0.0, 1.0))

# =====================================================================
# Engulfing (inchangé)
# =====================================================================

def is_bullish_engulfing(df: pd.DataFrame, lookback: int = 3) -> bool:
    if df is None or len(df) < 2:
        return False

    o1, c1 = float(df["open"].iloc[-2]), float(df["close"].iloc[-2])
    o2, c2 = float(df["open"].iloc[-1]), float(df["close"].iloc[-1])

    if c1 >= o1 or c2 <= o2:
        return False

    body1_low, body1_high = min(o1, c1), max(o1, c1)
    body2_low, body2_high = min(o2, c2), max(o2, c2)

    return body2_low <= body1_low and body2_high >= body1_high


def is_bearish_engulfing(df: pd.DataFrame, lookback: int = 3) -> bool:
    if df is None or len(df) < 2:
        return False

    o1, c1 = float(df["open"].iloc[-2]), float(df["close"].iloc[-2])
    o2, c2 = float(df["open"].iloc[-1]), float(df["close"].iloc[-1])

    if c1 <= o1 or c2 >= o2:
        return False

    body1_low, body1_high = min(o1, c1), max(o1, c1)
    body2_low, body2_high = min(o2, c2), max(o2, c2)

    return body2_low <= body1_low and body2_high >= body1_high


# =====================================================================
# ADD-ON INSTITUTIONNEL COMPLET
# =====================================================================

def _inst_sweeps(df: pd.DataFrame, tick: float = 0.0) -> Dict[str, bool]:
    if len(df) < 5:
        return {"sweep_high": False, "sweep_low": False}

    h1, h2 = df["high"].iloc[-1], df["high"].iloc[-2]
    l1, l2 = df["low"].iloc[-1], df["low"].iloc[-2]
    price = float(df["close"].iloc[-1])

    tol = _dynamic_tol(price, tick)

    sweep_high = abs(h2 - df["high"].iloc[-3]) <= tol * price and (h1 > h2)
    sweep_low = abs(l2 - df["low"].iloc[-3]) <= tol * price and (l1 < l2)

    return {"sweep_high": sweep_high, "sweep_low": sweep_low}


def _inst_fvg(df: pd.DataFrame) -> Dict[str, bool]:
    n = len(df)
    up = False
    down = False
    for i in range(max(3, n - 60), n - 1):
        if df["low"].iloc[i] > df["high"].iloc[i - 2]:
            up = True
        if df["high"].iloc[i] < df["low"].iloc[i - 2]:
            down = True
    return {"up_fvg": up, "down_fvg": down}


def _discount_premium(df: pd.DataFrame, bias: str, lookback: int = 60):
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
    last_bos_pos = None
    close = float(df["close"].iloc[-1])

    for s in reversed(swings):
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

    # Pullback institutionnel
    if side == "LONG":
        if bos_dir == "UP" and discount and fvg["up_fvg"]:
            return "inst_pullback"
    else:
        if bos_dir == "DOWN" and premium and fvg["down_fvg"]:
            return "inst_pullback"

    # Continuation institutionnelle
    if age_bos is not None and age_bos <= 4:
        if side == "LONG" and fvg["up_fvg"] and bos_dir == "UP":
            return "inst_continuation"
        if side == "SHORT" and fvg["down_fvg"] and bos_dir == "DOWN":
            return "inst_continuation"

    return "none"


# =====================================================================
# MODULE ADD-ON FINAL
# =====================================================================

def _institutional_addon(
    df: pd.DataFrame,
    base_ctx: Dict[str, Any],
    bias: Optional[str],
    tick: float = 0.0,
    oi_series: Optional[pd.Series] = None,
    cvd_series: Optional[pd.Series] = None,
) -> Dict[str, Any]:

    sweeps = _inst_sweeps(df, tick)
    fvg = _inst_fvg(df)

    discount = premium = False
    if bias:
        discount, premium = _discount_premium(df, bias)

    age = _age_since_last_bos(df, base_ctx.get("swings", []))

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
