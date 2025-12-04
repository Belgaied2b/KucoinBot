# ============================================================
# institutional_data.py — DESK LEAD INSTITUTIONNEL v3
# Analyse institutionnelle robuste (OI / CVD / Funding / Liquidity)
# ============================================================

from __future__ import annotations

import time
import logging
import requests
import numpy as np
import pandas as pd
import random
from typing import Optional, Dict, Any
from functools import lru_cache

LOGGER = logging.getLogger(__name__)

# -----------------------------------------------------------
# ENDPOINTS BINANCE
# -----------------------------------------------------------
BINANCE_FUTURES_API = "https://fapi.binance.com/fapi/v1"
BINANCE_FUTURES_DATA = "https://fapi.binance.com/futures/data"

# -----------------------------------------------------------
# CACHE SYMBOLS BINANCE
# -----------------------------------------------------------
_BINANCE_SYMS_CACHE = {"ts": 0.0, "set": set()}
_BINANCE_SYMS_TTL = 900  # 15 min

# -----------------------------------------------------------
# RATE LIMIT — TOKEN BUCKET ADAPTATIF
# -----------------------------------------------------------
_RATE: Dict[str, Dict[str, float]] = {}
_BASE = 0.12
_MAX = 2.0
_TOKENS = 3.0

def _rate_state(ep: str):
    st = _RATE.get(ep)
    if st is None:
        st = {"interval": _BASE, "tokens": _TOKENS, "last": time.time()}
        _RATE[ep] = st
    return st

def _adaptive_wait(ep: str):
    st = _rate_state(ep)
    now = time.time()

    added = (now - st["last"]) / st["interval"]
    st["tokens"] = min(_TOKENS, st["tokens"] + added)
    st["last"] = now

    if st["tokens"] >= 1:
        st["tokens"] -= 1
        return

    need = 1 - st["tokens"]
    delay = need * st["interval"]
    time.sleep(delay)

def _ok(ep):
    st = _rate_state(ep)
    st["interval"] = max(_BASE, st["interval"] * 0.9)

def _fail(ep, retry_after: Optional[float] = None):
    st = _rate_state(ep)
    new_int = st["interval"] * 1.6
    if retry_after:
        new_int = max(new_int, retry_after)
    st["interval"] = min(_MAX, new_int)
    time.sleep(random.uniform(0.05, 0.25) * st["interval"])

# -----------------------------------------------------------
# HTTP CLIENT
# -----------------------------------------------------------
_SESSION = requests.Session()
_SESSION.headers.update({"User-Agent": "desk-lead-bot/2.0"})

def _ep_key(url: str):
    try:
        return url.split("/fapi/")[1].split("?")[0]
    except:
        return "?"

# -----------------------------------------------------------
# SAFE JSON REQUEST
# -----------------------------------------------------------
def _safe_json_get(url: str, params=None, timeout=6, retries=2):
    ep = _ep_key(url)
    for i in range(retries + 1):
        try:
            _adaptive_wait(ep)
            r = _SESSION.get(url, params=params or {}, timeout=timeout)

            if r.status_code == 200:
                try:
                    js = r.json()
                except:
                    LOGGER.warning("[INST] JSON parse failed %s", url)
                    js = None
                _ok(ep)
                return js

            if r.status_code == 429:
                ra = r.headers.get("Retry-After")
                try:
                    ra = float(ra)
                except:
                    ra = None
                _fail(ep, retry_after=ra)
                continue

            if 500 <= r.status_code < 600:
                _fail(ep)
                continue

            LOGGER.warning("[INST] HTTP %s for %s", r.status_code, url)

        except Exception as e:
            LOGGER.warning("[INST] Exception %s on %s", e, url)
            _fail(ep)

        time.sleep(0.20 * (i + 1))

    return None

# -----------------------------------------------------------
# BINANCE SYMBOLS
# -----------------------------------------------------------
def _refresh_syms():
    now = time.time()
    if now - _BINANCE_SYMS_CACHE["ts"] < _BINANCE_SYMS_TTL:
        return

    data = _safe_json_get(f"{BINANCE_FUTURES_API}/exchangeInfo")
    syms = set()

    try:
        for s in (data or {}).get("symbols", []):
            if s.get("contractType") in ("PERPETUAL", "CURRENT_QUARTER", "NEXT_QUARTER"):
                sym = str(s.get("symbol", "")).upper()
                if sym.endswith("USDT"):
                    syms.add(sym)
    except:
        pass

    _BINANCE_SYMS_CACHE["set"] = syms
    _BINANCE_SYMS_CACHE["ts"] = now

_ALIAS = {"XBT": "BTC", "APR": "APE"}

@lru_cache(maxsize=1024)
def _map_sym(ku: str) -> Optional[str]:
    if not ku:
        return None
    s = ku.upper()

    for suf in ("USDTM", "-USDTM", "USDT-PERP", "PERP", "M"):
        if s.endswith(suf):
            s = s[: -len(suf)]
            break

    base = s.replace("USDT", "")
    base = _ALIAS.get(base, base)
    b = f"{base}USDT"

    _refresh_syms()
    return b if b in _BINANCE_SYMS_CACHE["set"] else None

# -----------------------------------------------------------
# LARGE TRADER RATIO (Net long/short imbalance)
# -----------------------------------------------------------
def get_large_trader_ratio(sym: str) -> float:
    b = _map_sym(sym)
    if not b:
        return 0.5

    data = _safe_json_get(
        f"{BINANCE_FUTURES_DATA}/topLongShortAccountRatio",
        params={"symbol": b, "period": "1h", "limit": 1}
    )
    try:
        if isinstance(data, list) and data:
            row = data[0]
            long_ac = float(row.get("longAccount", 0))
            short_ac = float(row.get("shortAccount", 0))
            if long_ac <= 0 and short_ac <= 0:
                return 0.5
            ratio = long_ac / max(short_ac, 1e-12)
            # Normalisation institutionnelle
            return float(np.clip((ratio - 1) / 4 + 0.5, 0, 1))
    except:
        pass

    return 0.5

# -----------------------------------------------------------
# FUNDING RATE
# -----------------------------------------------------------
try:
    from settings import FUNDING_DEADBAND
except:
    FUNDING_DEADBAND = 0.00005

def get_funding_rate(sym: str) -> Optional[float]:
    b = _map_sym(sym)
    if not b:
        return None
    data = _safe_json_get(f"{BINANCE_FUTURES_API}/premiumIndex", params={"symbol": b})
    try:
        return float(data.get("lastFundingRate"))
    except:
        return None

# -----------------------------------------------------------
# CVD DIVERGENCE (robuste)
# -----------------------------------------------------------
def get_cvd_divergence(sym: str, limit=600) -> float:
    b = _map_sym(sym)
    if not b:
        return 0.0

    data = _safe_json_get(
        f"{BINANCE_FUTURES_API}/aggTrades",
        params={"symbol": b, "limit": limit}
    )

    if not isinstance(data, list) or len(data) < 50:
        return 0.0

    try:
        df = pd.DataFrame(data)
        df["p"] = pd.to_numeric(df["p"], errors="coerce")
        df["q"] = pd.to_numeric(df["q"], errors="coerce")
        df["m"] = df["m"].astype(bool)
        df = df.dropna()

        df["delta"] = df.apply(lambda r: r["q"] * (-1 if r["m"] else 1), axis=1)
        cvd = df["delta"].cumsum().iloc[-1]

        price_chg = df["p"].iloc[-1] - df["p"].iloc[0]
        if abs(price_chg) < 1e-12:
            return 0.0

        # score directionnel : cohérent (1) / incohérent (-1)
        return float(np.sign(price_chg) * np.sign(cvd))
    except:
        return 0.0

# -----------------------------------------------------------
# LIQUIDITY CLUSTERS
# -----------------------------------------------------------
def detect_liquidity_clusters(df: pd.DataFrame, lookback=80, tol=0.0004):
    try:
        highs = df["high"].tail(lookback).astype(float).values
        lows = df["low"].tail(lookback).astype(float).values
    except:
        return {"eq_highs": [], "eq_lows": []}

    eqh = []
    eql = []

    for i in range(1, len(highs)):
        if abs(highs[i] - highs[i-1]) / highs[i] <= tol:
            eqh.append(highs[i])
        if abs(lows[i] - lows[i-1]) / lows[i] <= tol:
            eql.append(lows[i])

    return {
        "eq_highs": sorted(set(round(float(x), 6) for x in eqh)),
        "eq_lows": sorted(set(round(float(x), 6) for x in eql)),
    }

# -----------------------------------------------------------
# INSTITUTIONAL PRESSURE (nouveau)
# -----------------------------------------------------------
def compute_institutional_pressure(symbol: str, bias: str) -> float:
    """
    Pression directionnelle institutionnelle :
      0 = aucune pression
      1 = pression totale alignée
    """
    large = get_large_trader_ratio(symbol)
    cvd = get_cvd_divergence(symbol)
    fund = get_funding_rate(symbol)

    score = 0

    if bias == "LONG":
        if large > 0.55: score += 1
        if cvd >= 0: score += 1
        if fund is not None and fund >= FUNDING_DEADBAND: score += 1

    if bias == "SHORT":
        if large < 0.45: score += 1
        if cvd <= 0: score += 1
        if fund is not None and fund <= -FUNDING_DEADBAND: score += 1

    return score / 3.0

# -----------------------------------------------------------
# SCORE GLOBAL INSTITUTIONNEL
# -----------------------------------------------------------
def compute_institutional_score(symbol: str, bias: str):
    large = get_large_trader_ratio(symbol)
    cvd = get_cvd_divergence(symbol)
    fund = get_funding_rate(symbol)

    scores = {
        "oi": 1 if (large > 0.55 if bias == "LONG" else large < 0.45) else 0,
        "cvd": 1 if (cvd >= 0 if bias == "LONG" else cvd <= 0) else 0,
        "fund": 0,
    }

    if fund is not None:
        if bias == "LONG" and fund >= FUNDING_DEADBAND:
            scores["fund"] = 1
        if bias == "SHORT" and fund <= -FUNDING_DEADBAND:
            scores["fund"] = 1

    return {
        "scores": scores,
        "score_total": scores["oi"] + scores["cvd"] + scores["fund"],
        "details": {
            "large_ratio": round(large, 3),
            "cvd_div": cvd,
            "funding_rate": fund,
            "bias": bias,
        }
    }

# -----------------------------------------------------------
# ORCHESTRATION COMPLETE (Desk Lead)
# -----------------------------------------------------------
def compute_full_institutional_analysis(symbol: str, bias: str):
    b = _map_sym(symbol)
    if not b:
        return {
            "institutional_score": 0,
            "institutional_strength": "Faible",
            "institutional_comment": "Symbole non mappable vers Binance",
            "neutral": True,
            "details": {},
        }

    inst = compute_institutional_score(symbol, bias)
    total = inst["score_total"]

    comments = []
    if inst["scores"]["oi"]: comments.append("OI cohérent")
    if inst["scores"]["cvd"]: comments.append("CVD directionnel")
    if inst["scores"]["fund"]: comments.append("Funding aligné")

    strength = "Fort" if total == 3 else "Moyen" if total == 2 else "Faible"

    return {
        "institutional_score": total,
        "institutional_strength": strength,
        "institutional_comment": ", ".join(comments) if comments else "Pas de flux dominants",
        "neutral": False,
        "details": inst,
        "pressure": compute_institutional_pressure(symbol, bias),
    }
