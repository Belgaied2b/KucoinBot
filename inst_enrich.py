# -*- coding: utf-8 -*-
"""
inst_enrich.py — Enrichissement 'institutionnel' (OI / funding / delta / liq)
- OI & funding via Binance Futures
- Delta via aggTrades incrémental (fenêtre temps)
- Liquidity proxy via KuCoin 1m (notional 5m)
- Cache TTL par symbole
"""

from __future__ import annotations
import os, time, collections
from typing import Dict, Any, Optional, Tuple

import httpx
import pandas as pd

from kucoin_utils import fetch_klines  # proxy liq 1m

BINANCE_FAPI = "https://fapi.binance.com"
_HTTP_TIMEOUT = float(os.getenv("INST_HTTP_TIMEOUT", "6.0"))

# Réfs de normalisation
OI_DELTA_REF          = float(os.getenv("OI_DELTA_REF", "0.004"))        # 0.4% variation 5m
FUND_REF              = float(os.getenv("FUND_REF", "0.00008"))          # 8 bps
DELTA_NOTIONAL_REF    = float(os.getenv("DELTA_NOTIONAL_REF", "150000")) # 150k USD (fenêtre)
LIQ_NOTIONAL_5M_REF   = float(os.getenv("LIQ_5M_REF", "500000"))         # 500k USD
INST_REFRESH_SEC      = int(os.getenv("INST_REFRESH_SEC", "30"))         # TTL cache

# Poids score global
W_OI   = float(os.getenv("W_OI", "0.6"))
W_DLT  = float(os.getenv("W_DELTA", "0.2"))
W_FUND = float(os.getenv("W_FUND", "0.2"))
W_LIQ  = float(os.getenv("W_LIQ", "0.5"))

# Fenêtre delta (secondes)
DELTA_WINDOW_SEC = int(os.getenv("DELTA_WINDOW_SEC", "300"))  # 5 min

# Cache snapshots
_CACHE: Dict[str, Dict[str, Any]] = {}

def _client() -> httpx.Client:
    return httpx.Client(timeout=_HTTP_TIMEOUT, headers={"Accept": "application/json","User-Agent":"inst/1.1"})

def _norm01(x: float, ref: float) -> float:
    if ref <= 0: return 0.0
    try:
        v = abs(float(x)) / float(ref)
    except Exception:
        return 0.0
    return max(0.0, min(1.0, v))

def map_symbol_to_binance(symbol: str) -> Optional[str]:
    s = symbol.upper().strip()
    if s.endswith("USDTM"): s = s[:-1]
    s = s.replace("XBT", "BTC")
    if not s.endswith("USDT"): return None
    return s

# ---------- Delta incrémental (aggTrades) ----------
class _DeltaCVD:
    def __init__(self, window_sec: int):
        self.window_ms = int(window_sec * 1000)
        self.state: Dict[str, Dict[str, Any]] = {}  # bsym -> {last_id:int|None, deq:deque[(ts,int notion_signed)]}

    def _trim(self, deq: "collections.deque"):
        cutoff = int(time.time() * 1000) - self.window_ms
        while deq and deq[0][0] < cutoff:
            deq.popleft()

    def _fetch(self, bsym: str, from_id: Optional[int]) -> list:
        params = {"symbol": bsym, "limit": 1000}
        if from_id is not None:
            params["fromId"] = int(from_id)
        try:
            with _client() as c:
                r = c.get(f"{BINANCE_FAPI}/fapi/v1/aggTrades", params=params)
                if r.status_code != 200: return []
                return r.json() or []
        except Exception:
            return []

    def update(self, bsym: str) -> Dict[str, float]:
        st = self.state.get(bsym)
        if st is None:
            st = {"last_id": None, "deq": collections.deque()}
            self.state[bsym] = st

        trades = self._fetch(bsym, st["last_id"] + 1 if st["last_id"] is not None else None)
        for t in trades:
            try:
                tid = int(t.get("a")); ts = int(t.get("T"))
                p = float(t.get("p", 0.0)); q = float(t.get("q", 0.0))
                is_buyer_maker = bool(t.get("m", False))
            except Exception:
                continue
            notion = p * q
            signed = -notion if is_buyer_maker else +notion
            st["deq"].append((ts, signed))
            st["last_id"] = tid

        self._trim(st["deq"])

        total = 0.0; buy_n = 0.0; sell_n = 0.0
        for _, val in st["deq"]:
            total += val
            if val >= 0: buy_n += val
            else: sell_n += (-val)

        score = _norm01(total, DELTA_NOTIONAL_REF)
        return {"delta_score": score, "cvd_usd": total, "buy_usd": buy_n, "sell_usd": sell_n}

_DELTA = _DeltaCVD(DELTA_WINDOW_SEC)

# ---------- OI / Funding ----------
def _fetch_binance_oi_score(bsym: str) -> Optional[float]:
    params = {"symbol": bsym, "period": "5m", "limit": 2}
    try:
        with _client() as c:
            r = c.get(f"{BINANCE_FAPI}/futures/data/openInterestHist", params=params)
            if r.status_code != 200: return None
            arr = r.json() or []
    except Exception:
        return None
    if len(arr) < 2: return None
    try:
        a, b = arr[-2], arr[-1]
        oi1 = float(a.get("sumOpenInterest", a.get("openInterest", 0)) or 0)
        oi2 = float(b.get("sumOpenInterest", b.get("openInterest", 0)) or 0)
        if oi1 <= 0: return None
        delta_pct = (oi2 - oi1) / oi1
        return _norm01(delta_pct, OI_DELTA_REF)
    except Exception:
        return None

def _fetch_binance_funding_score(bsym: str) -> Optional[float]:
    params = {"symbol": bsym, "limit": 1}
    try:
        with _client() as c:
            r = c.get(f"{BINANCE_FAPI}/fapi/v1/fundingRate", params=params)
            if r.status_code != 200: return None
            arr = r.json() or []
    except Exception:
        return None
    if not arr: return None
    try:
        fr = float(arr[-1].get("fundingRate", 0.0))
        return _norm01(fr, FUND_REF)
    except Exception:
        return None

# ---------- Liquidity proxy ----------
def _fetch_liq_score_kucoin(symbol: str) -> Tuple[Optional[float], Optional[float]]:
    try:
        df = fetch_klines(symbol, interval="1m", limit=6)
    except Exception:
        return None, None
    if df is None or len(df) < 2:
        return None, None
    try:
        sub = df.tail(5)
        notional = float((sub["close"] * sub["volume"]).sum())
        liq_score = _norm01(notional, LIQ_NOTIONAL_5M_REF)
        return liq_score, notional
    except Exception:
        return None, None

# ---------- Public API ----------
def get_institutional_snapshot(symbol: str) -> Dict[str, Any]:
    now = time.time()
    ent = _CACHE.get(symbol)
    if ent and (now - float(ent.get("ts", 0))) < INST_REFRESH_SEC:
        return ent["data"]

    bsym = map_symbol_to_binance(symbol)
    oi_sc   = _fetch_binance_oi_score(bsym) if bsym else None
    fund_sc = _fetch_binance_funding_score(bsym) if bsym else None
    dlt     = _DELTA.update(bsym) if bsym else {"delta_score": 0.0, "cvd_usd": 0.0, "buy_usd": 0.0, "sell_usd": 0.0}
    liq_sc, liq5 = _fetch_liq_score_kucoin(symbol)

    total = 0.0; w_sum = 0.0
    if oi_sc is not None:   total += W_OI   * float(oi_sc);   w_sum += W_OI
    if dlt is not None:     total += W_DLT  * float(dlt.get("delta_score", 0.0)); w_sum += W_DLT
    if fund_sc is not None: total += W_FUND * float(fund_sc); w_sum += W_FUND
    if liq_sc is not None:  total += W_LIQ  * float(liq_sc);  w_sum += W_LIQ
    score = float(total) if w_sum > 0 else 0.0

    out = {
        "oi_score": float(oi_sc or 0.0),
        "delta_score": float((dlt or {}).get("delta_score", 0.0)),
        "funding_score": float(fund_sc or 0.0),
        "liq_score": float(liq_sc or 0.0),
        "delta_cvd_usd": float((dlt or {}).get("cvd_usd", 0.0)),
        "liq_notional_5m": float(liq5 or 0.0),
        "score": score,
    }
    _CACHE[symbol] = {"ts": now, "data": out}
    return out
