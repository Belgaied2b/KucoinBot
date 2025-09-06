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
        try:
            _logger.info(msg)
            return
        except Exception:
            pass
    print(msg, flush=True)

def _log_warn(msg: str):
    if _logger:
        try:
            _logger.warning(msg)
            return
        except Exception:
            pass
    print(msg, flush=True)

def _log_exc(prefix: str, e: Exception):
    if _logger:
        try:
            _logger.exception(f"{prefix} error: {e}")
            return
        except Exception:
            pass
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
    if s.endswith("USDTM"):
        s = s.replace("USDTM", "USDT")
    if s.endswith(".P"):
        s = s.replace(".P", "")
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
            return arr
    except Exception as e:
        _log_exc(f"[OI-HIST] {b_symbol}", e)
    return []

def get_oi_score(symbol: str, price_series: Optional[List[float]] = None) -> float:
    hist = get_open_interest_hist(symbol, limit=5)
    if len(hist) < 2:
        return 0.0
    try:
        prev = float(hist[-2].get("sumOpenInterest", hist[-2].get("openInterest", 0.0)) or 0.0)
        last = float(hist[-1].get("sumOpenInterest", hist[-1].get("openInterest", 0.0)) or 0.0)
        if prev <= 0:
            return 0.0
        delta_pct = (last - prev) / prev
        base = _clamp(abs(delta_pct) / 0.005)  # Institutionnel : 0.5% OI move = score 1
        align = 1.0
        if price_series and len(price_series) >= 2:
            pr_delta = price_series[-1] - price_series[-2]
            if delta_pct * pr_delta < 0:
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
                score = _clamp(z / 3.0)
            else:
                score = _clamp(abs(fr) / 0.0001)  # Institutionnel : 0.01% = score 1
            return score
    except Exception as e:
        _log_exc(f"[FundingScore] {b_symbol}", e)
    return 0.0

# --------------------------------------------------------------------------------------
# Liquidations (WebSocket exact + normalisation volume 5m réel)
# --------------------------------------------------------------------------------------
try:
    from binance_ws import get_liquidations_notional_5m
    USE_WS = True
except Exception:
    USE_WS = False
    _log_warn("[Liq] binance_ws non disponible, aucune liquidation comptée")

def _get_avg_vol_5m(symbol: str) -> float:
    """Calcule le volume moyen 5m à partir du quoteVolume 24h Binance."""
    b_symbol = map_symbol_to_binance(symbol)
    try:
        r = _get(f"{BASE}/fapi/v1/ticker/24hr", {"symbol": b_symbol}, timeout=6.0)
        if r.status_code == 200:
            data = r.json() or {}
            vol_24h = float(data.get("quoteVolume", 0.0) or 0.0)
            return max(1.0, vol_24h / 288.0)  # 288 * 5m = 24h
    except Exception as e:
        _log_exc(f"[AvgVol5m] {b_symbol}", e)
    return 1e6  # fallback

def get_liq_pack(symbol: str) -> Dict[str, Any]:
    b_symbol = map_symbol_to_binance(symbol)
    avg_vol_5m = _get_avg_vol_5m(b_symbol)

    if USE_WS:
        try:
            notional = get_liquidations_notional_5m(b_symbol)  # somme brute USDT
            score = _clamp(notional / avg_vol_5m)
            return {
                "liq_new_score": score,
                "liq_score": score,
                "liq_notional_5m": notional,
                "liq_imbalance_5m": notional / avg_vol_5m,  # ratio direct
                "liq_source": "ws_forceOrder"
            }
        except Exception as e:
            _log_exc(f"[Liq-WS] {b_symbol}", e)

    return {
        "liq_new_score": 0.0,
        "liq_score": 0.0,
        "liq_notional_5m": 0.0,
        "liq_imbalance_5m": 0.0,
        "liq_source": "none"
    }

# --------------------------------------------------------------------------------------
# CVD (Delta volume via aggTrades)
# --------------------------------------------------------------------------------------
def get_cvd_score(symbol: str, limit: int = 500) -> float:
    b_symbol = map_symbol_to_binance(symbol)
    try:
        r = _get(f"{BASE}/fapi/v1/aggTrades", {"symbol": b_symbol, "limit": limit}, timeout=6.0)
        if r.status_code == 200:
            trades = r.json() or []
            buy_vol = sum(float(t["q"]) for t in trades if t.get("m") == False)
            sell_vol = sum(float(t["q"]) for t in trades if t.get("m") == True)
            tot = buy_vol + sell_vol
            if tot <= 0:
                return 0.0
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
            return float(data.get("data", {}).get("total_market_cap", {}).get("usd", 0.0) or 0.0)
    except Exception as e:
        _log_exc("[Macro] TOTAL", e)
    return 0.0

def get_macro_btc_dominance() -> float:
    try:
        r = _get(f"{CG}/global", timeout=8.0)
        if r.status_code == 200:
            data = r.json()
            dom_pct = float(data.get("data", {}).get("market_cap_percentage", {}).get("btc", 0.0) or 0.0)
            return dom_pct / 100.0
    except
