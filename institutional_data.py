# =====================================================================
# institutional_data.py — Institutional Intelligence Engine
# Desk Lead version — OI / Funding / CVD / Liquidations (Binance)
# Fully compatible with analyze_signal.py
# =====================================================================

import aiohttp
import numpy as np
from typing import Dict, Any


BINANCE_FUTURES = "https://fapi.binance.com"


# ================================================================
# Helpers
# ================================================================

async def _fetch(session, url, params=None):
    try:
        async with session.get(url, params=params, timeout=5) as r:
            return await r.json()
    except Exception:
        return {}


def _slope(series, lookback=5):
    """Simple slope estimator."""
    if series is None or len(series) < lookback + 1:
        return 0.0
    return float(series[-1] - series[-lookback]) / max(abs(series[-lookback]), 1e-9)


# ================================================================
# Fetchers — Binance
# ================================================================

async def fetch_oi(session, symbol: str):
    url = BINANCE_FUTURES + "/futures/data/openInterestHist"
    r = await _fetch(session, url, params={"symbol": symbol, "period": "5m", "limit": 30})
    try:
        return [float(x["sumOpenInterest"]) for x in r]
    except:
        return None


async def fetch_funding(session, symbol: str):
    url = BINANCE_FUTURES + "/fapi/v1/fundingRate"
    r = await _fetch(session, url, params={"symbol": symbol, "limit": 20})
    try:
        return float(r[-1]["fundingRate"])
    except:
        return 0.0


async def fetch_cvd(session, symbol: str):
    url = BINANCE_FUTURES + "/fapi/v1/depth"
    r = await _fetch(session, url, params={"symbol": symbol, "limit": 100})
    try:
        bids = sum(float(b[1]) for b in r["bids"])
        asks = sum(float(a[1]) for a in r["asks"])
        return bids - asks
    except:
        return 0.0


async def fetch_liquidations(session, symbol: str):
    url = BINANCE_FUTURES + "/futures/data/liquidationOrders"
    r = await _fetch(session, url, params={"symbol": symbol, "limit": 50"})
    try:
        buys = sum(float(x["executedQty"]) for x in r if x["side"] == "BUY")
        sells = sum(float(x["executedQty"]) for x in r if x["side"] == "SELL")
        return buys, sells
    except:
        return 0.0, 0.0


# ================================================================
# Institutional Analysis — Main function
# ================================================================

async def compute_full_institutional_analysis(symbol: str, bias: str) -> Dict[str, Any]:
    """
    Retourne :
        - OI_slope
        - CVD_slope
        - funding
        - liquidation imbalance
        - score institutionnel (0..3)
        - pressure directionnelle
    """

    binance_symbol = symbol.replace("_UMCBL", "").upper()  # EX: BTCUSDT

    async with aiohttp.ClientSession() as session:

        oi = await fetch_oi(session, binance_symbol)
        cvd = await fetch_cvd(session, binance_symbol)
        funding = await fetch_funding(session, binance_symbol)
        liq_buy, liq_sell = await fetch_liquidations(session, binance_symbol)

    # Slopes
    oi_slope = _slope(oi) if oi else 0.0
    cvd_slope = cvd  # cvd = real-time imbalance

    # Liquidation imbalance
    liq_pressure = (liq_buy - liq_sell) / max(liq_buy + liq_sell + 1e-9, 1)

    # =========================
    # SCORE INSTITUTIONNEL
    # =========================
    score = 0

    # 1) Open Interest directionnel
    if (bias == "LONG" and oi_slope > 0) or (bias == "SHORT" and oi_slope < 0):
        score += 1

    # 2) CVD directionnel
    if (bias == "LONG" and cvd_slope > 0) or (bias == "SHORT" and cvd_slope < 0):
        score += 1

    # 3) Funding aligned
    if (bias == "LONG" and funding > 0) or (bias == "SHORT" and funding < 0):
        score += 1

    # Pressure combines cvd & liquidation imbalance
    pressure = 0.5 * np.tanh(cvd_slope / 5000) + 0.5 * liq_pressure

    return {
        "oi_slope": oi_slope,
        "cvd_slope": cvd_slope,
        "funding": funding,
        "liq_buy": liq_buy,
        "liq_sell": liq_sell,
        "liq_pressure": liq_pressure,

        "pressure": float(pressure),
        "institutional_score": score,   # utilisé par analyze_signal
        "details": {
            "oi_trend": oi_slope,
            "cvd": cvd_slope,
            "funding": funding,
            "liq_pressure": liq_pressure,
        }
    }
