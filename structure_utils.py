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
#   ✔ + CONFIRMATION BOS institutionnel (volume/OI/CVD)
#   ✔ + CHoCH institutionnel (trend shift + BOS confirmé)
#
#   → 100% rétro-compatible avec analyze_signal.py et scanner.py
# ============================================================

from __future__ import annotations

import numpy as np
import pandas as pd
from typing import Optional, List, Dict, Any

# ============================================================
# UTILITAIRES TOLERANCE & EGALITES
# ============================================================

def _equal(a: float, b: float, tol: float = 0.0003) -> bool:
    """
    Vérifie l'égalité (equal highs/lows) avec une tolérance dynamique.
    """
    return abs(a - b) <= tol * max(a, b, 1e-8)


# ============================================================
# PIVOTS & SWINGS
# ============================================================

def _detect_pivots(df: pd.DataFrame, left: int = 2, right: int = 2, max_bars: int = 300):
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
    pivots = []

    for i in range(start, end):
        if highs[i] == float(highs[i - left: i + right + 1].max()):
            pivots.append({"pos": i, "kind": "high", "price": float(highs[i])})
        if lows[i] == float(lows[i - left: i + right + 1].min()):
            pivots.append({"pos": i, "kind": "low", "price": float(lows[i])})

    pivots.sort(key=lambda p: p["pos"])
    if not pivots:
        return []

    # compression
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
            elif p["kind"] == "low" and p["price"] <= last["price"]:
                comp[-1] = p

    return comp[-max_bars:]


def _build_swings(df: pd.DataFrame, left: int = 2, right: int = 2, max_pivots: int = 50):
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

        swings.append({
            "pos": int(p["pos"]),
            "kind": p["kind"],
            "price": float(p["price"]),
            "label": label,
        })

    return swings


# ============================================================
# TREND DETECTION
# ============================================================

def _trend_from_labels(labels: List[str]) -> str:
    if not labels:
        return "unknown"

    from collections import Counter
    c = Counter([x for x in labels if x])

    hh = c.get("HH", 0)
    hl = c.get("HL", 0)
    lh = c.get("LH", 0)
    ll = c.get("LL", 0)

    up = hh + hl
    down = ll + lh

    if up >= 2 and down == 0:
        return "up"
    if down >= 2 and up == 0:
        return "down"
    if up == 0 and down == 0:
        return "unknown"
    return "range"


# ============================================================
# BOS CONFIRMATION — INSTITUTIONNEL
# ============================================================

def _institutional_bos_confirm(df, bos_dir, oi_series=None, cvd_series=None):
    """
    Confirmation BOS : volume + OI + CVD si disponibles.
    N'affecte pas la logique originale, seulement renforce.
    """
    if bos_dir not in ("UP", "DOWN"):
        return bos_dir

    # Volume
    try:
        vol = df["volume"].astype(float)
        vol_ok = vol.iloc[-1] >= vol.rolling(30).mean().iloc[-1]
    except:
        vol_ok = True

    # OI
    oi_ok = True
    if oi_series is not None:
        try:
            o = oi_series.astype(float).tail(5)
            oi_ok = (o.iloc[-1] - o.iloc[0]) >= 0
        except:
            pass

    # CVD
    cvd_ok = True
    if cvd_series is not None:
        try:
            c = cvd_series.astype(float).tail(5)
            cvd_ok = (c.iloc[-1] - c.iloc[0]) >= 0 if bos_dir == "UP" else (c.iloc[-1] - c.iloc[0]) <= 0
        except:
            pass

    return bos_dir if (vol_ok and oi_ok and cvd_ok) else None


# ============================================================
# ANALYZE STRUCTURE (AVEC ADD-ON INSTITUTIONNEL)
# ============================================================

def analyze_structure(df: pd.DataFrame, bias: Optional[str] = None, left=2, right=2, max_pivots=50,
                      oi_series=None, cvd_series=None) -> Dict[str, Any]:

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

    # dernier swing high/low
    last_high = last_low = None
    for s in swings:
        if s["pos"] >= len(df) - 1:
            continue
        if s["kind"] == "high":
            last_high = s if (last_high is None or s["pos"] > last_high["pos"]) else last_high
        else:
            last_low = s if (last_low is None or s["pos"] > last_low["pos"]) else last_low

    bos_dir = None
    if last_high and close > last_high["price"]:
        bos_dir = "UP"
    elif last_low and close < last_low["price"]:
        bos_dir = "DOWN"

    # confirmation institutionnelle
    bos_dir = _institutional_bos_confirm(df, bos_dir, oi_series, cvd_series)
    out["bos_direction"] = bos_dir

    # trend
    labels = [s["label"] for s in swings]
    trend = _trend_from_labels(labels)
    out["trend_state"] = trend
    prev_trend = _trend_from_labels(labels[:-2]) if len(labels) >= 4 else "unknown"

    choch = None
    cos = None
    last = None

    # CHOCH institutionnel
    if bos_dir and prev_trend in ("up", "down") and trend in ("up", "down") and prev_trend != trend:
        choch = "UP" if trend == "up" else "DOWN"
        last = f"choch_{trend}"

    elif prev_trend in ("up", "down") and trend == "range":
        cos = "trend_to_range"
        last = "cos_trend_to_range"

    elif prev_trend == "range" and trend in ("up", "down"):
        cos = "range_to_trend"
        last = "cos_range_to_trend"

    elif bos_dir:
        last = f"bos_{bos_dir.lower()}"

    out["choch_direction"] = choch
    out["cos"] = cos
    out["last_event"] = last

    # phase
    if trend == "up":
        out["phase"] = "expansion" if bos_dir == "UP" else "pullback"
    elif trend == "down":
        out["phase"] = "expansion" if bos_dir == "DOWN" else "pullback"
    elif trend == "range":
        out["phase"] = "distribution" if prev_trend == "up" else (
            "accumulation" if prev_trend == "down" else "range"
        )

    # ADD-ON institutionnel
    inst = _institutional_addon(df, out, bias)
    out.update(inst)

    return out


# ============================================================
# LEGACY (NE PAS MODIFIER)
# ============================================================

def detect_bos(df, lookback: int = 10):
    ctx = analyze_structure(df)
    bos = ctx.get("bos_direction")
    if bos == "UP":
        return "BOS_UP"
    if bos == "DOWN":
        return "BOS_DOWN"
    return None


def structure_valid(df, bias: str, lookback: int = 10) -> bool:
    ctx = analyze_structure(df, bias)
    bos = ctx.get("bos_direction")
    trend = ctx.get("trend_state")
    b = (bias or "").upper()

    if b == "LONG":
        return bos == "UP" or trend == "up"
    if b == "SHORT":
        return bos == "DOWN" or trend == "down"
    return True

# =====================================================================
# HTF Trend — EMA 20/50 (LEGACY REQUIRED BY analyze_signal.py)
# =====================================================================

def _ema(x: pd.Series, n: int = 20) -> pd.Series:
    """
    EMA simple pour le filtre HTF.
    """
    return x.ewm(span=n, adjust=False).mean()


def htf_trend_ok(df_htf: Optional[pd.DataFrame], bias: str) -> bool:
    """
    Filtre HTF utilisé par analyze_signal.py.
    Confirme que la tendance H4/H1 soutient le biais de trade.
    - LONG : close > EMA50 et EMA20 > EMA50
    - SHORT : close < EMA50 et EMA20 < EMA50
    """
    if df_htf is None or len(df_htf) < 60:
        return True  # fallback permissif

    close = df_htf["close"].astype(float)
    ema20 = _ema(close, 20)
    ema50 = _ema(close, 50)

    if str(bias).upper() == "LONG":
        return bool(close.iloc[-1] > ema50.iloc[-1] and ema20.iloc[-1] > ema50.iloc[-1])

    if str(bias).upper() == "SHORT":
        return bool(close.iloc[-1] < ema50.iloc[-1] and ema20.iloc[-1] < ema50.iloc[-1])

    return True

# ============================================================
# Engulfing (legacy)
# ============================================================

def is_bullish_engulfing(df, lookback=3):
    if df is None or len(df) < 2:
        return False

    o1, c1 = df["open"].iloc[-2], df["close"].iloc[-2]
    o2, c2 = df["open"].iloc[-1], df["close"].iloc[-1]

    if c1 >= o1 or c2 <= o2:
        return False

    return (min(o2, c2) <= min(o1, c1)) and (max(o2, c2) >= max(o1, c1))


def is_bearish_engulfing(df, lookback=3):
    if df is None or len(df) < 2:
        return False

    o1, c1 = df["open"].iloc[-2], df["close"].iloc[-2]
    o2, c2 = df["open"].iloc[-1], df["close"].iloc[-1]

    if c1 <= o1 or c2 >= o2:
        return False

    return (min(o2, c2) <= min(o1, c1)) and (max(o2, c2) >= max(o1, c1))


# ============================================================
# INSTITUTIONAL ADD-ON
# ============================================================

def _inst_sweeps(df: pd.DataFrame):
    if len(df) < 5:
        return {"sweep_high": False, "sweep_low": False}

    h1, h2, h3 = df["high"].iloc[-1], df["high"].iloc[-2], df["high"].iloc[-3]
    l1, l2, l3 = df["low"].iloc[-1], df["low"].iloc[-2], df["low"].iloc[-3]

    sweep_high = _equal(h2, h3) and (h1 > h2)
    sweep_low = _equal(l2, l3) and (l1 < l2)

    return {"sweep_high": sweep_high, "sweep_low": sweep_low}


def _inst_fvg(df: pd.DataFrame):
    n = len(df)
    up = False
    down = False
    for i in range(max(3, n - 30), n - 1):
        if df["low"].iloc[i] > df["high"].iloc[i - 2]:
            up = True
        if df["high"].iloc[i] < df["low"].iloc[i - 2]:
            down = True
    return {"up_fvg": up, "down_fvg": down}


def _discount_premium(df: pd.DataFrame, bias: str, lookback=50):
    sub = df.tail(lookback)
    hi = float(sub["high"].max())
    lo = float(sub["low"].min())
    mid = 0.5 * (hi + lo)
    close = float(df["close"].iloc[-1])

    if bias.upper() == "LONG":
        return close < mid, False
    else:
        return False, close > mid


def _age_since_last_bos(df: pd.DataFrame, swings: List[Dict[str, Any]]):
    close = float(df["close"].iloc[-1])
    for s in reversed(swings):
        if s["kind"] == "high" and close > s["price"]:
            return len(df) - 1 - s["pos"]
        if s["kind"] == "low" and close < s["price"]:
            return len(df) - 1 - s["pos"]
    return None


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
        if side == "LONG" and bos_dir == "UP" and fvg["up_fvg"]:
            return "inst_continuation"
        if side == "SHORT" and bos_dir == "DOWN" and fvg["down_fvg"]:
            return "inst_continuation"

    return "none"


def _institutional_addon(df, base_ctx, bias):
    sweeps = _inst_sweeps(df)
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
            age=age,
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
