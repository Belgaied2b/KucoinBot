# ============================================================
# structure_utils.py — VERSION FUSION DESK LEAD (ADD-ON FINAL)
# ============================================================
# CONTENU :
#   ✔ Pivots, swings, HH/HL/LH/LL
#   ✔ trend_state, CHoCH, COS, phase
#   ✔ Engulfing patterns
#   ✔ BOS quality + liquidity + OI
#   ✔ Commitment (OI + CVD)
#   ✔ HTF Trend OK (EMA20/50) — requis par analyze_signal.py
#   ✔ structure_valid + detect_bos (compatibilité legacy)
#   ✔ Add-on institutionnel complet :
#       • sweeps
#       • FVG
#       • discount/premium
#       • age_since_bos
#       • setup_type (pullback / continuation / sweep reversal)
#
#  → 100% compatible analyze_signal.py / scanner.py
# ============================================================

from __future__ import annotations

import numpy as np
import pandas as pd
from typing import Optional, List, Dict, Any

# ============================================================
# PIVOTS & SWINGS
# ============================================================

def _detect_pivots(df, left=2, right=2, max_bars=300):
    if df is None or len(df) < left + right + 3:
        return []

    try:
        highs = df["high"].astype(float).to_numpy()
        lows = df["low"].astype(float).to_numpy()
    except Exception:
        return []

    n = len(df)
    start = max(left, n - max_bars)
    end = n - right

    pivots = []
    for i in range(start, end):
        if highs[i] == highs[i-left:i+right+1].max():
            pivots.append({"pos": i, "kind": "high", "price": float(highs[i])})
        if lows[i] == lows[i-left:i+right+1].min():
            pivots.append({"pos": i, "kind": "low", "price": float(lows[i])})

    if not pivots:
        return []

    pivots.sort(key=lambda p: p["pos"])

    # compression
    out = []
    for p in pivots:
        if not out:
            out.append(p)
            continue
        last = out[-1]
        if p["kind"] != last["kind"]:
            out.append(p)
        else:
            if p["kind"] == "high":
                if p["price"] >= last["price"]:
                    out[-1] = p
            else:
                if p["price"] <= last["price"]:
                    out[-1] = p

    return out[-max_bars:]


def _build_swings(df, left=2, right=2, max_pivots=50):
    pivots = _detect_pivots(df, left, right, max_pivots * 3)
    if not pivots:
        return []

    swings = []
    last_high = None
    last_low = None

    for p in pivots[-max_pivots:]:
        if p["kind"] == "high":
            label = "H" if last_high is None else ("HH" if p["price"] > last_high else "LH")
            last_high = p["price"]
        else:
            label = "L" if last_low is None else ("HL" if p["price"] > last_low else "LL")
            last_low = p["price"]

        swings.append(
            {"pos": int(p["pos"]), "kind": p["kind"], "price": float(p["price"]), "label": label}
        )

    return swings


def _trend_from_labels(labels: List[str]) -> str:
    if not labels:
        return "unknown"

    from collections import Counter
    c = Counter([x for x in labels if x])

    up_score = c.get("HH", 0) + c.get("HL", 0)
    down_score = c.get("LH", 0) + c.get("LL", 0)

    if up_score >= 2 and c.get("LL", 0) == 0 and up_score >= down_score:
        return "up"
    if down_score >= 2 and c.get("HH", 0) == 0 and down_score >= up_score:
        return "down"
    if up_score == 0 and down_score == 0:
        return "unknown"
    return "range"

# ============================================================
# MAIN STRUCTURE ANALYSIS
# ============================================================

def analyze_structure(df, bias=None, left=2, right=2, max_pivots=50):
    ctx = {
        "swings": [],
        "bos_direction": None,
        "choch_direction": None,
        "trend_state": "unknown",
        "phase": "unknown",
        "cos": None,
        "last_event": None,
    }

    if df is None or len(df) < 10:
        return ctx

    swings = _build_swings(df, left, right, max_pivots)
    ctx["swings"] = swings
    if not swings:
        return ctx

    close = float(df["close"].iloc[-1])
    last_high = last_low = None

    for s in swings:
        if s["pos"] >= len(df) - 1:
            continue
        if s["kind"] == "high":
            if last_high is None or s["pos"] >= last_high["pos"]:
                last_high = s
        else:
            if last_low is None or s["pos"] >= last_low["pos"]:
                last_low = s

    # BOS detection
    bos = None
    if last_high and close > last_high["price"]:
        bos = "UP"
    elif last_low and close < last_low["price"]:
        bos = "DOWN"
    ctx["bos_direction"] = bos

    labels = [s["label"] for s in swings if s.get("label")]
    trend = _trend_from_labels(labels)
    ctx["trend_state"] = trend

    prev_trend = _trend_from_labels(labels[:-2]) if len(labels) >= 4 else "unknown"

    choch = cos = last_event = None
    if prev_trend in ("up", "down") and trend in ("up", "down") and prev_trend != trend:
        choch = "UP" if trend == "up" else "DOWN"
        last_event = f"choch_{trend}"
    elif prev_trend in ("up", "down") and trend == "range":
        cos = "trend_to_range"
        last_event = "cos_trend_to_range"
    elif prev_trend == "range" and trend in ("up", "down"):
        cos = "range_to_trend"
        last_event = "cos_range_to_trend"
    elif bos:
        last_event = f"bos_{bos.lower()}"

    ctx["choch_direction"] = choch
    ctx["cos"] = cos
    ctx["last_event"] = last_event

    # Phase mapping
    if trend == "up":
        ctx["phase"] = "expansion" if bos == "UP" else "pullback"
    elif trend == "down":
        ctx["phase"] = "expansion" if bos == "DOWN" else "pullback"
    elif trend == "range":
        ctx["phase"] = "distribution" if prev_trend == "up" else ("accumulation" if prev_trend == "down" else "unknown")

    # Add institutional metadata
    ctx.update(_institutional_addon(df, ctx, bias))

    return ctx

# ============================================================
# LEGACY API (REQUIRED BY analyze_signal.py)
# ============================================================

def detect_bos(df, lookback=10):
    ctx = analyze_structure(df)
    if ctx.get("bos_direction") == "UP":
        return "BOS_UP"
    if ctx.get("bos_direction") == "DOWN":
        return "BOS_DOWN"
    return None


def structure_valid(df, bias, lookback=10):
    ctx = analyze_structure(df, bias)
    b = (bias or "").upper()
    bos = ctx.get("bos_direction")
    trend = ctx.get("trend_state")

    if b == "LONG":
        return bos == "UP" or trend == "up"
    if b == "SHORT":
        return bos == "DOWN" or trend == "down"
    return True

# ============================================================
# HTF TREND FILTER (EMA20/50) — REQUIRED BY analyze_signal.py
# ============================================================

def _ema(x: pd.Series, n=20):
    return x.ewm(span=n, adjust=False).mean()


def htf_trend_ok(df_htf, bias):
    if df_htf is None or len(df_htf) < 60:
        return True

    close = df_htf["close"].astype(float)
    ema20 = _ema(close, 20)
    ema50 = _ema(close, 50)

    side = str(bias).upper()
    if side == "LONG":
        return close.iloc[-1] > ema50.iloc[-1] and ema20.iloc[-1] > ema50.iloc[-1]
    if side == "SHORT":
        return close.iloc[-1] < ema50.iloc[-1] and ema20.iloc[-1] < ema50.iloc[-1]
    return True

# ============================================================
# BOS QUALITY + LIQUIDITY + OI
# ============================================================

try:
    from institutional_data import detect_liquidity_clusters
except Exception:
    detect_liquidity_clusters = None


def bos_quality_details(
    df,
    oi_series=None,
    vol_lookback=60,
    vol_pct=0.8,
    oi_min_trend=0.003,
    oi_min_squeeze=-0.005,
    df_liq=None,
    price=None,
    tick=0.0,
):
    out = {
        "ok": True,
        "vol_ok": True,
        "oi_ok": True,
        "bos_direction": None,
        "has_liquidity_zone": False,
        "liquidity_side": None,
        "liq_distance": None,
        "liq_distance_bps": None,
    }

    if df is None or len(df) < 5:
        return out

    ctx = analyze_structure(df)
    out["bos_direction"] = ctx.get("bos_direction")

    # Volume
    try:
        vol = df["volume"].astype(float).tail(vol_lookback)
        v_last = float(vol.iloc[-1])
        thresh = float(vol.quantile(vol_pct))
        out["vol_ok"] = v_last >= thresh
    except:
        out["vol_ok"] = True

    # OI
    if oi_series is not None and len(oi_series) >= 3:
        try:
            o = oi_series.astype(float).tail(3)
            pct = (o.iloc[-1] - o.iloc[0]) / max(1e-12, o.iloc[0])
            out["oi_ok"] = pct >= oi_min_trend or pct <= oi_min_squeeze
        except:
            out["oi_ok"] = True

    out["ok"] = out["vol_ok"] and out["oi_ok"]

    # Liquidity zones
    ref_price = float(price) if price is not None else float(df["close"].iloc[-1])

    eq_highs = []
    eq_lows = []

    try:
        base_df = df_liq if isinstance(df_liq, pd.DataFrame) else df
        if detect_liquidity_clusters:
            liq = detect_liquidity_clusters(base_df, lookback=80, tol=0.0005)
            eq_highs = liq.get("eq_highs", [])
            eq_lows = liq.get("eq_lows", [])
    except:
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
            out["liq_distance"] = float(dist)
            out["liq_distance_bps"] = float((dist / max(ref_price, 1e-9)) * 10000)
            out["liquidity_side"] = side

    return out


def bos_quality_ok(df, oi_series=None,
                   vol_lookback=60,
                   vol_pct=0.8,
                   oi_min_trend=0.003,
                   oi_min_squeeze=-0.005):
    try:
        return bool(
            bos_quality_details(df, oi_series, vol_lookback, vol_pct,
                                oi_min_trend, oi_min_squeeze).get("ok", True)
        )
    except:
        return True

# ============================================================
# COMMITMENT SCORE
# ============================================================

def commitment_score(oi_series, cvd_series, lookback=80):
    try:
        oi = pd.Series(oi_series).dropna().tail(lookback)
        cvd = pd.Series(cvd_series).dropna().tail(lookback)
    except:
        return 0.5

    if len(oi) < 5 or len(cvd) < 5:
        return 0.5

    try:
        oi_delta = oi.iloc[-1] - oi.iloc[0]
        cvd_delta = cvd.iloc[-1] - cvd.iloc[0]
    except:
        return 0.5

    oi_norm = float(np.clip(oi_delta / max(abs(oi.iloc[0]), 1e-9), -3, 3))
    cvd_norm = float(np.clip(cvd_delta / max(abs(cvd.iloc[0]), 1e-9), -3, 3))

    mag = 0.5 * (abs(oi_norm) + abs(cvd_norm))
    mag_score = 1 - np.exp(-mag)
    same = oi_norm * cvd_norm > 0

    align = mag_score if same else -mag_score
    return float(np.clip(0.5 + 0.4 * align, 0, 1))

# ============================================================
# ENGULFING PATTERNS
# ============================================================

def is_bullish_engulfing(df):
    if df is None or len(df) < 2:
        return False

    o1, c1 = df["open"].iloc[-2], df["close"].iloc[-2]
    o2, c2 = df["open"].iloc[-1], df["close"].iloc[-1]

    if c1 >= o1 or c2 <= o2:
        return False

    return min(o2, c2) <= min(o1, c1) and max(o2, c2) >= max(o1, c1)


def is_bearish_engulfing(df):
    if df is None or len(df) < 2:
        return False

    o1, c1 = df["open"].iloc[-2], df["close"].iloc[-2]
    o2, c2 = df["open"].iloc[-1], df["close"].iloc[-1]

    if c1 <= o1 or c2 >= o2:
        return False

    return min(o2, c2) <= min(o1, c1) and max(o2, c2) >= max(o1, c1)

# ============================================================
# INSTITUTIONAL ADD-ON (Sweeps, FVG, Discount, Setup)
# ============================================================

def _equal(a, b, tol=0.0003):
    return abs(a - b) <= tol * max(a, b, 1e-9)


def _inst_sweeps(df):
    if len(df) < 5:
        return {"sweep_high": False, "sweep_low": False}

    h1, h2 = df["high"].iloc[-1], df["high"].iloc[-2]
    l1, l2 = df["low"].iloc[-1], df["low"].iloc[-2]

    eqh = _equal(h2, df["high"].iloc[-3])
    eql = _equal(l2, df["low"].iloc[-3])

    return {
        "sweep_high": eqh and (h1 > h2),
        "sweep_low": eql and (l1 < l2),
    }


def _inst_fvg(df):
    n = len(df)
    up = down = False
    for i in range(max(3, n - 30), n - 1):
        if df["low"].iloc[i] > df["high"].iloc[i - 2]:
            up = True
        if df["high"].iloc[i] < df["low"].iloc[i - 2]:
            down = True
    return {"up_fvg": up, "down_fvg": down}


def _discount_premium(df, bias, lookback=50):
    sub = df.tail(lookback)
    hi = float(sub["high"].max())
    lo = float(sub["low"].min())
    mid = 0.5 * (hi + lo)
    close = float(df["close"].iloc[-1])

    if bias.upper() == "LONG":
        return close < mid, False
    return False, close > mid


def _age_since_last_bos(df, swings):
    if not swings:
        return None

    close = float(df["close"].iloc[-1])
    for s in reversed(swings):
        if s["kind"] == "high" and close > s["price"]:
            return len(df) - 1 - s["pos"]
        if s["kind"] == "low" and close < s["price"]:
            return len(df) - 1 - s["pos"]

    return None


def _classify_institutional_setup(bias, sweeps, fvg, bos, discount, premium, age):
    side = bias.upper()

    # Sweep reversal
    if side == "LONG" and sweeps["sweep_low"]:
        return "inst_sweep_reversal"
    if side == "SHORT" and sweeps["sweep_high"]:
        return "inst_sweep_reversal"

    # Institutional Pullback
    if side == "LONG":
        if bos == "UP" and discount and fvg["up_fvg"]:
            return "inst_pullback"
    else:
        if bos == "DOWN" and premium and fvg["down_fvg"]:
            return "inst_pullback"

    # Continuation
    if age is not None and age <= 4:
        if side == "LONG" and bos == "UP" and fvg["up_fvg"]:
            return "inst_continuation"
        if side == "SHORT" and bos == "DOWN" and fvg["down_fvg"]:
            return "inst_continuation"

    return "none"


def _institutional_addon(df, ctx, bias):
    sweeps = _inst_sweeps(df)
    fvg = _inst_fvg(df)

    discount = premium = False
    if bias:
        try:
            discount, premium = _discount_premium(df, bias)
        except:
            pass

    age = None
    try:
        age = _age_since_last_bos(df, ctx.get("swings", []))
    except:
        pass

    setup = None
    if bias:
        setup = _classify_institutional_setup(
            bias,
            sweeps,
            fvg,
            ctx.get("bos_direction"),
            discount,
            premium,
            age,
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
