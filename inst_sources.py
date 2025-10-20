from __future__ import annotations
import os, time, logging
from typing import Dict, Any, List, Optional
import httpx

log = logging.getLogger("inst.src")
HTTP_TIMEOUT = float(os.getenv("HTTP_TIMEOUT", "10"))
RETRY = int(os.getenv("HTTP_RETRY", "2"))

def _client() -> httpx.Client:
    return httpx.Client(timeout=HTTP_TIMEOUT, headers={"User-Agent": "inst-pro/1.0"})

def _get(url: str, params: Optional[dict] = None) -> dict | list:
    err = None
    for _ in range(max(1, RETRY)):
        try:
            with _client() as c:
                r = c.get(url, params=params)
            if r.status_code == 200:
                return r.json()
            err = f"{r.status_code} {r.text[:200]}"
        except Exception as e:
            err = str(e)
        time.sleep(0.1)
    log.warning("GET fail %s params=%s err=%s", url, params, err)
    return {}

def _binance(sym: str) -> str:
    return sym.upper().replace("USDTM","USDT").replace("USDCM","USDC")

def funding_rates(sym: str, limit: int = 24) -> List[float]:
    b = _binance(sym)
    js = _get("https://fapi.binance.com/fapi/v1/fundingRate", {"symbol": b, "limit": limit})
    try:
        return [float(x["fundingRate"]) for x in js]
    except Exception:
        return []

def open_interest_hist(sym: str, period: str = "5m", limit: int = 96) -> List[float]:
    b = _binance(sym)
    js = _get("https://fapi.binance.com/futures/data/openInterestHist",
              {"symbol": b, "period": period, "limit": limit})
    try:
        return [float(x["sumOpenInterest"]) for x in js]
    except Exception:
        return []

def long_short_ratio(sym: str, period: str = "5m", limit: int = 96) -> List[float]:
    b = _binance(sym)
    js = _get("https://fapi.binance.com/futures/data/topLongShortAccountRatio",
              {"symbol": b, "period": period, "limit": limit})
    try:
        return [float(x["longShortRatio"]) for x in js]
    except Exception:
        return []

def klines(sym: str, interval: str = "5m", limit: int = 240) -> List[List]:
    b = _binance(sym)
    js = _get("https://fapi.binance.com/fapi/v1/klines",
              {"symbol": b, "interval": interval, "limit": limit})
    if isinstance(js, list): return js
    return []
