# -*- coding: utf-8 -*-
import httpx
import statistics
from typing import Optional, Dict, Any, List

from logger_utils import get_logger

# --------------------------------------------------------------------------------------
# Logger
# --------------------------------------------------------------------------------------
try:
    _logger = get_logger("institutional_data")
except Exception:
    _logger = None

def _log_info(msg: str):
    if _logger:
        try: _logger.info(msg); return
        except Exception: pass
    print(msg, flush=True)

def _log_warn(msg: str):
    if _logger:
        try: _logger.warning(msg); return
        except Exception: pass
    print(msg, flush=True)

def _log_exc(prefix: str, e: Exception):
    if _logger:
        try: _logger.exception(f"{prefix} error: {e}"); return
        except Exception: pass
    print(f"{prefix} error: {e}", flush=True)

# --------------------------------------------------------------------------------------
# HTTP
# --------------------------------------------------------------------------------------
BASE = "https://fapi.binance.com"   # Binance Futures USDT-M
CG   = "https://api.coingecko.com/api/v3"

def _get(url: str, params: Optional[dict] = None, timeout: float = 6.0) -> httpx.Response:
    return httpx.get(url, params=params or {}, timeout=timeout, headers={"Accept": "application/json"})

# --------------------------------------------------------------------------------------
# Utils
# --------------------------------------------------------------------------------------
def map_symbol_to_binance(sym: str) -> str:
    s = (sym or "").upper()
    if s.endswith("USDTM"): s = s.replace("USDTM", "USDT")
    if s.endswith(".P"): s = s.replace(".P", "")
    return s

def _clamp(x: float, lo: float = 0.0, hi: float = 1.0) -> float:
    return max(lo, min(hi, x))

# --------------------------------------------------------------------------------------
# Open Interest
# --------------------------------------------------------------------------------------
def get_open_interest_hist(symbol: str, period: str = "5m", limit: int = 20) -> List[Dict[str, Any]]:
    b_symbol = map_symbol_to_binance(symbol)
    try:
        r = _get(f"{BASE}/futures/data/openInterestHist",
                 {"symbol": b_symbol, "period": period, "limit": limit}, timeout=6.0)
        if r.status_code == 200:
            arr = r.json() or []
            _log_info(f"[OI-HIST] {b_symbol} len={len(arr)}")
            return arr
        _log_warn(f"[OI-HIST] {b_symbol} HTTP {r.status_code}")
    except Exception as e:
        _log_exc(f"[OI-HIST] {b_symbol}", e)
    return []

def get_oi_score(symbol: str, price_series: Optional[List[float]] = None) -> float:
    hist = get_open_interest_hist(symbol, limit=5)
    if len(hist) < 2: return 0.0
    try:
        prev = float(hist[-2].get("sumOpenInterest", hist[-2].get("openInterest", 0.0)) or 0.0)
        last = float(hist[-1].get("sumOpenInterest", hist[-1].get("openInterest", 0.0)) or 0.0)
        if prev <= 0: return 0.0
        delta_pct = (last - prev) / prev
        base = _clamp(abs(delta_pct) / 0.02)  # 2% move -> score=1
        align = 1.0
        if price_series and len(price_series) >= 2:
            pr_delta = price_series[-1] - price_series[-2]
            if delta_pct * pr_delta < 0:  # OI ↑ mais prix ↓ => piégeur
                align = 0.3
        return _clamp(base * align)
    except Exception:
        return 0.0

# --------------------------------------------------------------------------------------
# Funding
# --------------------------------------------------------------------------------------
def get_funding_score(symbol: str, history: Optional[List[float]] = None) -> float:
    b_symbol = map_symbol_to_binance(symbol)
    try:
        r = _get(f"{BASE}/fapi/v1/premiumIndex", {"symbol": b_symbol}, timeout=6.0)
        if r.status_code == 200:
            data = r.json() or {}
            fr = float(data.get("lastFundingRate", 0.0) or 0.0)
            if history and len(history) >= 10:
                mean = statistics.mean(history)
                stdev = statistics.pstdev(history) or 1e-6
                z = abs((fr - mean) / stdev)
                score = _clamp(z / 3.0)  # z=3 -> score=1
            else:
                score = _clamp(abs(fr) / 0.00025)  # 0.025% -> score=1
            _log_info(f"[FundingScore] {b_symbol} fr={fr} score={score:.3f}")
            return score
    except Exception as e:
        _log_exc(f"[FundingScore] {b_symbol}", e)
    return 0.0

# --------------------------------------------------------------------------------------
# Liquidations (WS + fallback proxy)
# --------------------------------------------------------------------------------------
try:
    from binance_ws import get_liquidations_notional_5m
    USE_WS = True
except Exception:
    USE_WS = False
    _log_warn("[Liq] binance_ws non disponible, fallback REST proxy")

def get_liq_pack(symbol: str, avg_vol_5m: float = 1e6) -> Dict[str, Any]:
    b_symbol = map_symbol_to_binance(symbol)

    # 1) Essai WebSocket (forceOrder, notionnel réel)
    if USE_WS:
        try:
            notional = get_liquidations_notional_5m(b_symbol)
            score = _clamp(notional / max(1.0, avg_vol_5m))
            return {
                "liq_new_score": score,
                "liq_score": score,
                "liq_notional_5m": notional,
                "liq_imbalance_5m": 0.0,
                "liq_source": "ws_forceOrder"
            }
        except Exception as e:
            _log_exc(f"[Liq-WS] {b_symbol}", e)

    # 2) Fallback REST proxy (takerLongShortRatio)
    try:
        rr = _get(f"{BASE}/futures/data/takerlongshortRatio",
                  {"symbol": b_symbol, "period": "5m", "limit": 1}, timeout=6.0)
        if rr.status_code == 200:
            recs = rr.json() or []
            if not recs:
                return {"liq_new_score":0.0,"liq_score":0.0,"liq_notional_5m":0.0,
                        "liq_imbalance_5m":0.0,"liq_source":"none"}
            rec = recs[-1]
            buy = float(rec.get("buyVol",0.0)); sell = float(rec.get("sellVol",0.0))
            imb = abs(buy-sell); denom = max(1.0,buy+sell)
            imb_ratio = imb/denom
            try:
                mark = float(_get(f"{BASE}/fapi/v1/premiumIndex",
                                  {"symbol":b_symbol}).json().get("markPrice",0.0))
            except: mark = 1.0
            notionnel = imb * mark
            score = _clamp(notionnel / max(1.0, avg_vol_5m))
            return {
                "liq_new_score": score,
                "liq_score": score,
                "liq_notional_5m": notionnel,
                "liq_imbalance_5m": imb_ratio,
                "liq_source": "proxy"
            }
    except Exception as e:
        _log_exc(f"[Liq-Proxy] {b_symbol}", e)

    return {"liq_new_score":0.0,"liq_score":0.0,"liq_notional_5m":0.0,
            "liq_imbalance_5m":0.0,"liq_source":"none"}

# --------------------------------------------------------------------------------------
# CVD (Delta volume via aggTrades)
# --------------------------------------------------------------------------------------
def get_cvd_score(symbol: str, limit: int = 500) -> float:
    b_symbol = map_symbol_to_binance(symbol)
    try:
        r = _get(f"{BASE}/fapi/v1/aggTrades", {"symbol": b_symbol, "limit": limit}, timeout=6.0)
        if r.status_code == 200:
            trades = r.json() or []
            buy_vol = sum(float(t["q"]) for t in trades if t.get("m")==False)  # buyer initiated
            sell_vol= sum(float(t["q"]) for t in trades if t.get("m")==True)   # seller initiated
            tot = buy_vol + sell_vol
            if tot <= 0: return 0.0
            delta = (buy_vol - sell_vol) / tot
            return _clamp(abs(delta))
    except Exception as e:
        _log_exc(f"[CVD] {b_symbol}", e)
    return 0.0

# --------------------------------------------------------------------------------------
# Macro (CoinGecko)
# --------------------------------------------------------------------------------------
def get_macro_total_mcap() -> float:
    try:
        r = _get(f"{CG}/global", timeout=8.0)
        if r.status_code == 200:
            data = r.json()
            return float(data.get("data",{}).get("total_market_cap",{}).get("usd",0.0) or 0.0)
    except Exception as e: _log_exc("[Macro] TOTAL", e)
    return 0.0

def get_macro_btc_dominance() -> float:
    try:
        r = _get(f"{CG}/global", timeout=8.0)
        if r.status_code == 200:
            data = r.json()
            dom_pct = float(data.get("data",{}).get("market_cap_percentage",{}).get("btc",0.0) or 0.0)
            return dom_pct/100.0
    except Exception as e: _log_exc("[Macro] DOM", e)
    return 0.0

def get_macro_total2() -> float:
    tot = get_macro_total_mcap()
    dom = get_macro_btc_dominance()
    return max(0.0, tot*(1-dom))

# --------------------------------------------------------------------------------------
# Snapshot
# --------------------------------------------------------------------------------------
def build_institutional_snapshot(symbol: str, price_series: Optional[List[float]]=None,
                                 funding_hist: Optional[List[float]]=None,
                                 avg_vol_5m: float=1e6) -> Dict[str, Any]:
    oi_s  = get_oi_score(symbol, price_series)
    fund_s= get_funding_score(symbol, funding_hist)
    liq_p = get_liq_pack(symbol, avg_vol_5m)
    cvd_s = get_cvd_score(symbol)

    score = (oi_s + fund_s + liq_p["liq_new_score"] + cvd_s) / 4.0

    return {
        "oi_score": oi_s,
        "funding_score": fund_s,
        "liq_new_score": liq_p["liq_new_score"],
        "cvd_score": cvd_s,
        "score": score,
        "meta": {
            "liq_notional_5m": liq_p["liq_notional_5m"],
            "liq_imbalance_5m": liq_p["liq_imbalance_5m"],
            "liq_source": liq_p["liq_source"],
        }
    }
