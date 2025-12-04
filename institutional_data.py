# ================================================
# institutional_data.py — Version A1 PRO
# Analyse institutionnelle robuste pour bot C-PRO
# ================================================
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

# ------------------------------------------
# ENDPOINTS BINANCE
# ------------------------------------------
BINANCE_FUTURES_API = "https://fapi.binance.com/fapi/v1"
BINANCE_FUTURES_DATA = "https://fapi.binance.com/futures/data"

# ------------------------------------------
# CACHE SYMBOLS BINANCE
# ------------------------------------------
_BINANCE_SYMS_CACHE = {"ts": 0.0, "set": set()}
_BINANCE_SYMS_TTL = 900  # 15 minutes

# ------------------------------------------
# ERROR COUNTERS (anti-spam logs)
# ------------------------------------------
_ERR_COUNTS = {"large_ratio": 0, "cvd_div": 0, "map": 0, "net": 0, "fund": 0}
_ERR_WARN_EVERY = 10

# ------------------------------------------
# RATE LIMIT ADAPTATIF (token bucket)
# ------------------------------------------
_RATE_STATE: Dict[str, Dict[str, float]] = {}
_BASE_INTERVAL = 0.15
_MAX_INTERVAL = 2.5
_BURST_TOKENS = 3.0

def _rate_state(ep: str):
    st = _RATE_STATE.get(ep)
    if st is None:
        st = {"interval": _BASE_INTERVAL, "tokens": _BURST_TOKENS, "last_ts": time.time()}
        _RATE_STATE[ep] = st
    return st

def _adaptive_wait(ep: str):
    st = _rate_state(ep)
    now = time.time()

    added = (now - st["last_ts"]) / max(st["interval"], 1e-12)
    st["tokens"] = min(_BURST_TOKENS, st["tokens"] + added)
    st["last_ts"] = now

    if st["tokens"] >= 1:
        st["tokens"] -= 1
        return

    need = 1 - st["tokens"]
    wait_s = need * st["interval"]
    if wait_s > 0:
        time.sleep(wait_s)

def _feedback_ok(ep: str):
    st = _rate_state(ep)
    st["interval"] = max(_BASE_INTERVAL, st["interval"] * 0.90)

def _feedback_backoff(ep: str, retry_after: Optional[float] = None):
    st = _rate_state(ep)
    new_int = st["interval"] * 1.6
    if retry_after:
        new_int = max(new_int, retry_after)
    st["interval"] = min(_MAX_INTERVAL, new_int)
    time.sleep(random.uniform(0.05, 0.25) * st["interval"])

# ------------------------------------------
# HTTP Session
# ------------------------------------------
_SESSION = requests.Session()
_SESSION.headers.update({"User-Agent": "insto-bot/1.1"})

def _endpoint_key(url: str):
    try:
        return url.split("/fapi/")[-1].split("?")[0].split("/")[-1]
    except:
        return "unknown"

# ------------------------------------------
# SAFE JSON GET (tolérant)
# ------------------------------------------
def _safe_json_get(url: str, params=None, timeout=6.0, retries=2):
    ep = _endpoint_key(url)
    for attempt in range(retries + 1):
        try:
            _adaptive_wait(ep)
            r = _SESSION.get(url, params=params or {}, timeout=timeout)

            if r.status_code == 200:
                try:
                    js = r.json()
                except Exception:
                    _bump("net", "Non-JSON response")
                    js = None

                _feedback_ok(ep)
                return js

            if r.status_code == 429:
                ra = r.headers.get("Retry-After")
                try:
                    ra = float(ra) if ra else None
                except:
                    ra = None
                _feedback_backoff(ep, retry_after=ra)
                continue

            if 500 <= r.status_code < 600:
                _feedback_backoff(ep)
                continue

            _bump("net", f"HTTP {r.status_code}")

        except Exception as e:
            _bump("net", f"Exception {e}")
            _feedback_backoff(ep)

        time.sleep(0.20 * (attempt + 1))

    return None

def _bump(key: str, msg: str):
    _ERR_COUNTS[key] += 1
    c = _ERR_COUNTS[key]
    if c % _ERR_WARN_EVERY == 0:
        LOGGER.warning("[INST] %s (x%s)", msg, c)
    else:
        LOGGER.debug("[INST] %s", msg)

# ------------------------------------------
# BINANCE SYMBOLS REFRESH
# ------------------------------------------
def _refresh_binance_symbols():
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

# ------------------------------------------
# KuCoin → Binance mapping
# ------------------------------------------
_ALIAS = {"XBT": "BTC", "APR": "APE"}

@lru_cache(maxsize=2048)
def _map_to_binance(ku_sym: str) -> Optional[str]:
    if not ku_sym:
        return None
    s = ku_sym.upper()

    for suf in ("USDTM", "USDT-PERP", "PERP", "M"):
        if s.endswith(suf):
            s = s[: -len(suf)]
            break

    if s.endswith("USDT"):
        base = s[:-4]
    else:
        base = s

    base = _ALIAS.get(base, base)
    b = f"{base}USDT"

    _refresh_binance_symbols()
    if b in _BINANCE_SYMS_CACHE["set"]:
        return b

    return None

# ------------------------------------------
# Large trader ratio
# ------------------------------------------
def get_large_trader_ratio(symbol: str) -> float:
    b = _map_to_binance(symbol)
    if not b:
        return 0.5

    data = _safe_json_get(
        f"{BINANCE_FUTURES_DATA}/topLongShortAccountRatio",
        params={"symbol": b, "period": "1h", "limit": 1}
    )
    try:
        if isinstance(data, list) and data:
            row = data[0]
            long_a = float(row.get("longAccount", 0))
            short_a = float(row.get("shortAccount", 0))
            if long_a <= 0 and short_a <= 0:
                return 0.5
            ratio = long_a / max(short_a, 1e-9)
            return float(np.clip(np.tanh(ratio), 0, 1))
    except:
        _bump("large_ratio", f"Parse failed {b}")

    return 0.5

# ------------------------------------------
# Funding rate
# ------------------------------------------
try:
    from settings import FUNDING_DEADBAND
except:
    FUNDING_DEADBAND = 0.00005

def get_funding_rate(symbol: str) -> Optional[float]:
    b = _map_to_binance(symbol)
    if not b:
        return None

    data = _safe_json_get(f"{BINANCE_FUTURES_API}/premiumIndex", params={"symbol": b})
    try:
        if isinstance(data, dict) and "lastFundingRate" in data:
            return float(data["lastFundingRate"])
    except:
        _bump("fund", f"Parse failed {b}")
    return None

# ------------------------------------------
# CVD divergence
# ------------------------------------------
def get_cvd_divergence(symbol: str, limit=500) -> float:
    b = _map_to_binance(symbol)
    if not b:
        return 0.0

    data = _safe_json_get(
        f"{BINANCE_FUTURES_API}/aggTrades",
        params={"symbol": b, "limit": limit}
    )
    if not isinstance(data, list) or len(data) < 20:
        return 0.0

    try:
        df = pd.DataFrame(data)
        if not {"p", "q", "m"}.issubset(df.columns):
            return 0.0

        df["p"] = pd.to_numeric(df["p"], errors="coerce")
        df["q"] = pd.to_numeric(df["q"], errors="coerce")
        df = df.dropna()

        df["side"] = df["m"].apply(lambda x: -1 if bool(x) else 1)
        df["delta"] = df["q"] * df["side"]

        cvd = df["delta"].sum()
        price_change = df["p"].iloc[-1] - df["p"].iloc[0]

        if abs(price_change) < 1e-9:
            return 0.0

        return float(np.clip(np.sign(price_change) * np.sign(cvd), -1, 1))
    except:
        _bump("cvd_div", f"Parse failed {b}")
        return 0.0

# ------------------------------------------
# Liquidity clusters (EQH/EQL)
# ------------------------------------------
def detect_liquidity_clusters(df: pd.DataFrame, lookback=60, tol=0.0005):
    try:
        highs = df["high"].tail(lookback).astype(float).values
        lows = df["low"].tail(lookback).astype(float).values
    except:
        return {"eq_highs": [], "eq_lows": []}

    eqh, eql = [], []
    for i in range(1, len(highs)):
        if abs(highs[i] - highs[i - 1]) / highs[i] < tol:
            eqh.append(highs[i])
        if abs(lows[i] - lows[i - 1]) / lows[i] < tol:
            eql.append(lows[i])

    return {
        "eq_highs": sorted({round(float(x), 6) for x in eqh}),
        "eq_lows": sorted({round(float(x), 6) for x in eql}),
    }

# ------------------------------------------
# SCORE GLOBAL
# ------------------------------------------
def compute_institutional_score(symbol: str, bias: str, prev_oi=None):
    large = get_large_trader_ratio(symbol)
    cvd = get_cvd_divergence(symbol)
    fund = get_funding_rate(symbol)

    oi_score = 1 if large > 0.55 else 0
    cvd_score = 1 if cvd >= 0 else 0
    fund_score = 0

    if fund is not None:
        if bias == "LONG" and fund >= FUNDING_DEADBAND:
            fund_score = 1
        if bias == "SHORT" and fund <= -FUNDING_DEADBAND:
            fund_score = 1

    total = oi_score + cvd_score + fund_score

    return {
        "scores": {"oi": oi_score, "cvd": cvd_score, "fund": fund_score},
        "score_total": total,
        "details": {
            "large_ratio": round(large, 3),
            "cvd_div": cvd,
            "funding_rate": fund,
            "bias": bias,
        },
    }

# ------------------------------------------
# ORCHESTRATION COMPLETE
# ------------------------------------------
def compute_full_institutional_analysis(symbol: str, bias: str, prev_oi=None):
    b = _map_to_binance(symbol)
    if not b:
        return {
            "institutional_score": 0,
            "institutional_strength": "Faible",
            "institutional_comment": "Pas de flux dominants",
            "neutral": True,
            "details": {},
        }

    inst = compute_institutional_score(symbol, bias, prev_oi)
    s = inst["scores"]
    total = inst["score_total"]

    c = []
    if s["oi"]:   c.append("OI↑")
    if s["fund"]: c.append("Funding cohérent")
    if s["cvd"]:  c.append("CVD cohérent")

    strength = "Fort" if total == 3 else "Moyen" if total == 2 else "Faible"

    return {
        "institutional_score": total,
        "institutional_strength": strength,
        "institutional_comment": ", ".join(c) if c else "Pas de flux dominants",
        "neutral": False,
        "details": inst,
    }
