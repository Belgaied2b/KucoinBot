# ============================================================
# structure_utils.py — VERSION FUSION DESK LEAD (INSTITUTIONNEL V3)
# ============================================================
# CONTIENT :
#   ✔ Moteur original (swings, pivots, HH/HL/LH/LL)
#   ✔ trend_state, CHoCH, COS, phase
#   ✔ bos_quality, commitment
#   ✔ Engulfing (bullish / bearish)
#   ✔ ADD-ON INSTITUTIONNEL COMPLET (Playbook C):
#       → sweeps institutionnels
#       → FVG institutionnels
#       → discount/premium zones
#       → BOS institutionnel (OI/CVD/volume/liquidité)
#       → CHoCH institutionnel
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
# OUTILS UTILES (tolerances dynamiques)
# =====================================================================

def _dynamic_tol(price: float, tick: float, bps_min: float = 4, ticks_min: int = 2) -> float:
    """Tolérance dynamique EQH/EQL."""
    price = float(max(price, 1e-12))
    tol_ticks = (ticks_min * tick) / price if tick > 0 else 0
    tol_bps = bps_min / 1e4
    return max(tol_ticks, tol_bps)


def _equals_dynamic(a: float, b: float, price: float, tick: float) -> bool:
    tol = _dynamic_tol(price, tick)
    return abs(a - b) <= tol * max(price, 1e-12)


def _detect_equal_highs_lows(df: pd.DataFrame, lookback: int = 50, tick: float = 0.0) -> Dict[str, List[float]]:
    highs = df["high"].tail(lookback).astype(float).values
    lows = df["low"].tail(lookback).astype(float).values

    close = float(df["close"].iloc[-1])
    eqh = set()
    eql = set()

    for i in range(1, len(highs)):
        if _equals_dynamic(highs[i], highs[i - 1], close, tick):
            eqh.add(round(float(highs[i]), 8))
        if _equals_dynamic(lows[i], lows[i - 1], close, tick):
            eql.add(round(float(lows[i]), 8))

    return {"eq_highs": sorted(eqh), "eq_lows": sorted(eql)}


# =====================================================================
# PIVOTS → SWINGS (HH/HL/LH/LL)
# =====================================================================

def _detect_pivots(df: pd.DataFrame, left=2, right=2, max_bars=300):
    n = len(df)
    if n < left + right + 3:
        return []

    highs = df["high"].astype(float).to_numpy()
    lows = df["low"].astype(float).to_numpy()

    pivots = []
    start = max(left, n - max_bars)
    end = n - right

    for i in range(start, end):
        if highs[i] == float(highs[i - left:i + right + 1].max()):
            pivots.append({"pos": i, "kind": "high", "price": float(highs[i])})
        if lows[i] == float(lows[i - left:i + right + 1].min()):
            pivots.append({"pos": i, "kind": "low", "price": float(lows[i])})

    if not pivots:
        return []

    # compression
    pivots.sort(key=lambda p: p["pos"])
    comp = []
    for p in pivots:
        if not comp:
            comp.append(p)
            continue
        last = comp[-1]
        if p["kind"] != last["kind"]:
            comp.append(p)
        else:
            if p["kind"] == "high" and p["price"] >= last["price"]:
                comp[-1] = p
            if p["kind"] == "low" and p["price"] <= last["price"]:
                comp[-1] = p

    return comp[-max_bars:]


def _build_swings(df, left=2, right=2, max_pivots=50):
    pivots = _detect_pivots(df, left, right, max_pivots * 3)
    if not pivots:
        return []

    swings = []
    last_h = None
    last_l = None

    for p in pivots[-max_pivots:]:
        label = None
        if p["kind"] == "high":
            label = "HH" if (last_h is not None and p["price"] > last_h) else "LH"
            if last_h is None:
                label = "H"
            last_h = p["price"]
        else:
            label = "HL" if (last_l is not None and p["price"] > last_l) else "LL"
            if last_l is None:
                label = "L"
            last_l = p["price"]

        swings.append(
            {"pos": p["pos"], "kind": p["kind"], "price": p["price"], "label": label}
        )
    return swings


def _trend_from_labels(labels: List[str]):
    if not labels:
        return "unknown"

    from collections import Counter
    c = Counter(labels)
    hh = c.get("HH", 0)
    hl = c.get("HL", 0)
    lh = c.get("LH", 0)
    ll = c.get("LL", 0)

    up = hh + hl
    down = ll + lh

    if up >= 2 and ll == 0 and up >= down:
        return "up"
    if down >= 2 and hh == 0 and down >= up:
        return "down"
    if up == 0 and down == 0:
        return "unknown"
    return "range"


# =====================================================================
# BOS / CHOCH / COS — CORE ENGINE
# =====================================================================

def institutional_bos_confirmation(bos_direction, df, oi_series, cvd_series, tick=0.0):
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

    confirmed = bool(vol_ok and oi_ok and cvd_ok)
    return {"confirmed": confirmed, "direction": bos_direction if confirmed else None}


def check_institutional_choch(new_trend, prev_trend, bos_inst):
    if not bos_inst["confirmed"]:
        return None
    if prev_trend in ("up", "down") and new_trend in ("up", "down"):
        if prev_trend != new_trend:
            return "UP" if new_trend == "up" else "DOWN"
    return None


def analyze_structure(
    df: pd.DataFrame,
    bias: Optional[str] = None,
    left: int = 2,
    right: int = 2,
    max_pivots: int = 50,
    tick: float = 0.0,
    oi_series: Optional[pd.Series] = None,
    cvd_series: Optional[pd.Series] = None,
):
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

    swings = _build_swings(df, left, right, max_pivots)
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
            last_high = s if (last_high is None or s["pos"] > last_high["pos"]) else last_high
        else:
            last_low = s if (last_low is None or s["pos"] > last_low["pos"]) else last_low

    bos = None
    if last_high and close > last_high["price"]:
        bos = "UP"
    elif last_low and close < last_low["price"]:
        bos = "DOWN"

    bos_inst = institutional_bos_confirmation(bos, df, oi_series, cvd_series, tick)
    if bos_inst["confirmed"]:
        bos = bos_inst["direction"]
    out["bos_direction"] = bos

    labels = [s["label"] for s in swings if s.get("label")]
    trend = _trend_from_labels(labels)
    out["trend_state"] = trend

    prev_trend = _trend_from_labels(labels[:-2]) if len(labels) >= 4 else "unknown"

    choch = check_institutional_choch(trend, prev_trend, bos_inst)
    cos = None
    last_evt = None

    if choch:
        last_evt = f"choch_{trend}"
    elif prev_trend in ("up", "down") and trend == "range":
        cos = "trend_to_range"
        last_evt = "cos_trend_to_range"
    elif prev_trend == "range" and trend in ("up", "down"):
        cos = "range_to_trend"
        last_evt = "cos_range_to_trend"
    elif bos:
        last_evt = f"bos_{bos.lower()}"

    out["choch_direction"] = choch
    out["cos"] = cos
    out["last_event"] = last_evt

    # PHASE
    if trend == "up":
        out["phase"] = "expansion" if bos == "UP" else "pullback"
    elif trend == "down":
        out["phase"] = "expansion" if bos == "DOWN" else "pullback"
    elif trend == "range":
        if prev_trend == "up":
            out["phase"] = "distribution"
        elif prev_trend == "down":
            out["phase"] = "accumulation"

    # INSTITUTIONAL ADD-ON
    inst = _institutional_addon(df, out, bias, tick, oi_series, cvd_series)
    out.update(inst)

    return out


# =====================================================================
# LEGACY COMPATIBILITY (ANALYZE_SIGNAL.PY)
# =====================================================================

def detect_bos(df: pd.DataFrame, lookback: int = 10):
    ctx = analyze_structure(df)
    bos = ctx.get("bos_direction")
    if bos == "UP":
        return "BOS_UP"
    if bos == "DOWN":
        return "BOS_DOWN"
    return None


def structure_valid(df: pd.DataFrame, bias: str, lookback: int = 10) -> bool:
    if df is None or len(df) < max(5, lookback):
        return True

    ctx = analyze_structure(df, bias=bias)
    bos = ctx.get("bos_direction")
    trend = ctx.get("trend_state")

    b = str(bias).upper()
    if b == "LONG":
        return bos == "UP" or trend == "up"
    if b == "SHORT":
        return bos == "DOWN" or trend == "down"

    return True


# =====================================================================
# Engulfing Patterns (BULLISH/BEARISH)
# =====================================================================

def is_bullish_engulfing(df, lookback=3):
    if df is None or len(df) < 2:
        return False

    o1, c1 = float(df["open"].iloc[-2]), float(df["close"].iloc[-2])
    o2, c2 = float(df["open"].iloc[-1]), float(df["close"].iloc[-1])

    if c1 >= o1 or c2 <= o2:
        return False

    return (min(o2, c2) <= min(o1, c1)) and (max(o2, c2) >= max(o1, c1))


def is_bearish_engulfing(df, lookback=3):
    if df is None or len(df) < 2:
        return False

    o1, c1 = float(df["open"].iloc[-2]), float(df["close"].iloc[-2])
    o2, c2 = float(df["open"].iloc[-1]), float(df["close"].iloc[-1])

    if c1 <= o1 or c2 >= o2:
        return False

    return (min(o2, c2) <= min(o1, c1)) and (max(o2, c2) >= max(o1, c1))


# =====================================================================
# Institutional Add-On
# =====================================================================

def _inst_sweeps(df, tick=0.0):
    if len(df) < 5:
        return {"sweep_high": False, "sweep_low": False}

    h1, h2 = df["high"].iloc[-1], df["high"].iloc[-2]
    l1, l2 = df["low"].iloc[-1], df["low"].iloc[-2]

    price = float(df["close"].iloc[-1])
    tol = _dynamic_tol(price, tick)

    sweep_h = abs(h2 - df["high"].iloc[-3]) <= tol * price and (h1 > h2)
    sweep_l = abs(l2 - df["low"].iloc[-3]) <= tol * price and (l1 < l2)

    return {"sweep_high": sweep_h, "sweep_low": sweep_l}


def _inst_fvg(df):
    n = len(df)
    up = False
    down = False

    for i in range(max(3, n - 60), n - 1):
        if df["low"].iloc[i] > df["high"].iloc[i - 2]:
            up = True
        if df["high"].iloc[i] < df["low"].iloc[i - 2]:
            down = True

    return {"up_fvg": up, "down_fvg": down}


def _discount_premium(df, bias, lookback=60):
    sub = df.tail(lookback)
    hi = float(sub["high"].max())
    lo = float(sub["low"].min())
    mid = (hi + lo) / 2
    close = float(df["close"].iloc[-1])

    if bias.upper() == "LONG":
        return close < mid, False
    else:
        return False, close > mid


def _age_since_last_bos(df, swings):
    close = float(df["close"].iloc[-1])
    last_bos = None

    for s in reversed(swings):
        if s["kind"] == "high" and close > s["price"]:
            last_bos = s["pos"]
            break
        if s["kind"] == "low" and close < s["price"]:
            last_bos = s["pos"]
            break

    if last_bos is None:
        return None

    return max(0, len(df) - 1 - last_bos)


def _classify_institutional_setup(bias, sweeps, fvg, bos_dir, discount, premium, age):
    side = bias.upper()

    if side == "LONG" and sweeps["sweep_low"]:
        return "inst_sweep_reversal"
    if side == "SHORT" and sweeps["sweep_high"]:
        return "inst_sweep_reversal"

    if side == "LONG" and bos_dir == "UP" and discount and fvg["up_fvg"]:
        return "inst_pullback"
    if side == "SHORT" and bos_dir == "DOWN" and premium and fvg["down_fvg"]:
        return "inst_pullback"

    if age is not None and age <= 4:
        if side == "LONG" and fvg["up_fvg"] and bos_dir == "UP":
            return "inst_continuation"
        if side == "SHORT" and fvg["down_fvg"] and bos_dir == "DOWN":
            return "inst_continuation"

    return "none"


def _institutional_addon(df, base_ctx, bias, tick, oi_series, cvd_series):
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
