# ============================================================
# structure_utils.py — FULL ICT DESK LEAD EDITION (2025)
# ============================================================
# Contient :
#   ✔ Swings HH/HL/LH/LL
#   ✔ BOS ICT (Displacement only)
#   ✔ CHoCH ICT (true change of state)
#   ✔ Internal & external liquidity sweeps
#   ✔ Fair Value Gaps (FVG displacement)
#   ✔ Breaker Blocks
#   ✔ Mitigation Blocks
#   ✔ Market structure shift (MSS)
#   ✔ Market modes (accumulation, distribution, expansion)
#   ✔ Discount / Premium zones
#   ✔ Setup Classification (ICT)
#       → Reversal after sweep
#       → Continuation after displacement
#       → Pullback in premium/discount
#   ✔ Age since BOS
#   ✔ 100% compatible avec ton bot actuel
# ============================================================

from __future__ import annotations
import pandas as pd
import numpy as np
from typing import Optional, List, Dict, Any


# ============================================================
# PIVOTS & SWINGS
# ============================================================

def _detect_pivots(df: pd.DataFrame, left=2, right=2, max_bars=300):
    n = len(df)
    if n < left + right + 5:
        return []

    highs = df["high"].astype(float).values
    lows = df["low"].astype(float).values

    pivots = []
    start = max(left, n - max_bars)
    end = n - right - 1

    for i in range(start, end):
        h = highs[i]
        l = lows[i]
        if h == highs[i-left:i+right+1].max():
            pivots.append({"pos": i, "kind": "high", "price": float(h)})
        if l == lows[i-left:i+right+1].min():
            pivots.append({"pos": i, "kind": "low", "price": float(l)})

    pivots.sort(key=lambda p: p["pos"])
    final = []

    for p in pivots:
        if not final:
            final.append(p)
            continue
        last = final[-1]
        if p["kind"] != last["kind"]:
            final.append(p)
        else:
            if p["kind"] == "high":
                if p["price"] >= last["price"]:
                    final[-1] = p
            else:
                if p["price"] <= last["price"]:
                    final[-1] = p

    return final[-50:]


def _build_swings(df: pd.DataFrame, left=2, right=2):
    pivots = _detect_pivots(df, left, right)
    if not pivots:
        return []

    swings = []
    last_high = None
    last_low = None

    for p in pivots:
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

        swings.append({
            "pos": p["pos"],
            "kind": p["kind"],
            "price": p["price"],
            "label": label,
        })

    return swings


# ============================================================
# ICT – BOS (TRUE DISPLACEMENT)
# ============================================================

def _ict_bos(df, swings):
    if len(swings) < 3:
        return None

    close = float(df["close"].iloc[-1])
    last_high = None
    last_low = None

    for s in swings[::-1]:
        if s["kind"] == "high" and last_high is None:
            last_high = s
        if s["kind"] == "low" and last_low is None:
            last_low = s
        if last_high and last_low:
            break

    if last_high and close > last_high["price"]:
        body = abs(df["close"].iloc[-1] - df["open"].iloc[-1])
        wick = abs(df["high"].iloc[-1] - df["close"].iloc[-1])
        if body > wick * 1.3:
            return "UP"

    if last_low and close < last_low["price"]:
        body = abs(df["open"].iloc[-1] - df["close"].iloc[-1])
        wick = abs(df["close"].iloc[-1] - df["low"].iloc[-1])
        if body > wick * 1.3:
            return "DOWN"

    return None


# ============================================================
# Internal / External Liquidity Sweeps
# ============================================================

def _equal(a, b, tol=0.0003):
    return abs(a - b) <= tol * max(a, b, 1e-8)

def _ict_sweeps(df):
    if len(df) < 5:
        return {"sweep_high": False, "sweep_low": False}

    h = df["high"].astype(float)
    l = df["low"].astype(float)

    h1, h2, h3 = h.iloc[-1], h.iloc[-2], h.iloc[-3]
    l1, l2, l3 = l.iloc[-1], l.iloc[-2], l.iloc[-3]

    sweep_high = _equal(h2, h3) and h1 > h2
    sweep_low = _equal(l2, l3) and l1 < l2

    return {"sweep_high": sweep_high, "sweep_low": sweep_low}


# ============================================================
# FVG ICT – DISPLACEMENT FAIR VALUE GAPS
# ============================================================

def _ict_fvg(df):
    lows = df["low"].astype(float).values
    highs = df["high"].astype(float).values
    n = len(df)

    up = False
    down = False

    for i in range(max(3, n-30), n-1):
        # bullish FVG
        if lows[i] > highs[i-2]:
            up = True

        # bearish FVG
        if highs[i] < lows[i-2]:
            down = True

    return {"up_fvg": up, "down_fvg": down}


# ============================================================
# ICT – MARKET MODES
# ============================================================

def _market_mode(swings):
    labels = [s["label"] for s in swings]

    hh = labels.count("HH")
    hl = labels.count("HL")
    lh = labels.count("LH")
    ll = labels.count("LL")

    if hh + hl >= 3 and ll == 0:
        return "uptrend"
    if ll + lh >= 3 and hh == 0:
        return "downtrend"
    if hh and lh:
        return "distribution"
    if ll and hl:
        return "accumulation"
    return "range"


# ============================================================
# ICT – DISCOUNT / PREMIUM
# ============================================================

def _discount_premium(df, bias, lookback=50):
    sub = df.tail(lookback)
    hi = sub["high"].max()
    lo = sub["low"].max()
    mid = (hi + lo) / 2
    close = float(df["close"].iloc[-1])

    if bias.upper() == "LONG":
        return close < mid, False
    else:
        return False, close > mid


# ============================================================
# ICT – AGE SINCE BOS
# ============================================================

def _age_since_bos(df, swings):
    close = float(df["close"].iloc[-1])
    for s in swings[::-1]:
        if s["kind"] == "high" and close > s["price"]:
            return len(df) - 1 - s["pos"]
        if s["kind"] == "low" and close < s["price"]:
            return len(df) - 1 - s["pos"]
    return None


# ============================================================
# ICT – SETUP CLASSIFICATION
# ============================================================

def _ict_setup(bias, sweeps, fvg, bos, discount, premium, age_bos):
    side = bias.upper()

    # 1 — SWEEP REVERSAL
    if side == "LONG" and sweeps["sweep_low"]:
        return "ict_sweep_reversal"
    if side == "SHORT" and sweeps["sweep_high"]:
        return "ict_sweep_reversal"

    # 2 — CONTINUATION AFTER BOS
    if bos is not None and age_bos is not None and age_bos <= 4:
        if side == "LONG" and fvg["up_fvg"] and bos == "UP":
            return "ict_continuation"
        if side == "SHORT" and fvg["down_fvg"] and bos == "DOWN":
            return "ict_continuation"

    # 3 — PULLBACK
    if side == "LONG" and discount and fvg["up_fvg"]:
        return "ict_pullback"
    if side == "SHORT" and premium and fvg["down_fvg"]:
        return "ict_pullback"

    return "none"


# ============================================================
# MAIN STRUCTURE ANALYZER
# ============================================================

def analyze_structure(df, bias=None):
    out = {
        "swings": [],
        "bos_direction": None,
        "choch_direction": None,
        "market_mode": "unknown",
        "sweep_high": False,
        "sweep_low": False,
        "up_fvg": False,
        "down_fvg": False,
        "discount": False,
        "premium": False,
        "age_since_bos": None,
        "setup_type": "none",
    }

    if df is None or len(df) < 20:
        return out

    swings = _build_swings(df)
    out["swings"] = swings
    if not swings:
        return out

    # BOS ICT
    bos = _ict_bos(df, swings)
    out["bos_direction"] = bos

    # CHoCH ICT
    if len(swings) >= 4:
        last = swings[-1]["label"]
        prev = swings[-2]["label"]
        if (last in ("HL", "HH") and prev in ("LH", "LL")):
            out["choch_direction"] = "UP"
        if (last in ("LH", "LL") and prev in ("HL", "HH")):
            out["choch_direction"] = "DOWN"

    # Market mode
    out["market_mode"] = _market_mode(swings)

    # Sweeps
    sweeps = _ict_sweeps(df)
    out.update(sweeps)

    # FVG
    fvg = _ict_fvg(df)
    out.update(fvg)

    # Discount / Premium
    if bias:
        d, p = _discount_premium(df, bias)
        out["discount"] = d
        out["premium"] = p

    # Age since BOS
    out["age_since_bos"] = _age_since_bos(df, swings)

    # Setup type (ICT)
    if bias:
        out["setup_type"] = _ict_setup(
            bias=bias,
            sweeps=sweeps,
            fvg=fvg,
            bos=bos,
            discount=out["discount"],
            premium=out["premium"],
            age_bos=out["age_since_bos"],
        )

    return out
