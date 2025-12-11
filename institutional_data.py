# =====================================================================
# institutional_data.py — Institutional Intelligence Engine
# Desk Lead version — OI / Funding / CVD / Liquidations (Binance Futures)
# Fully compatible avec analyze_signal.SignalAnalyzer
# =====================================================================

from typing import Dict, Any, List, Optional

import aiohttp
import numpy as np


BINANCE_FUTURES = "https://fapi.binance.com"


# =====================================================================
# HTTP helper
# =====================================================================

async def _fetch(session: aiohttp.ClientSession, path: str, params: Optional[dict] = None):
    """
    Helper fin : GET sur l'API Binance Futures, avec gestion d'erreurs soft.

    Retourne :
      - data JSON parsé si succès
      - None en cas d'erreur réseau / parsing
    """
    url = BINANCE_FUTURES + path
    try:
        async with session.get(url, params=params, timeout=5) as resp:
            if resp.status != 200:
                return None
            return await resp.json()
    except Exception:
        return None


# =====================================================================
# OI / Funding / CVD / Liquidations
# =====================================================================

async def _fetch_open_interest_hist(session: aiohttp.ClientSession, symbol: str, limit: int = 30) -> np.ndarray:
    """
    Essaie de récupérer un historique d'Open Interest via l'endpoint :
      /futures/data/openInterestHist

    Si indisponible ou erreur → renvoie un petit tableau vide.
    """
    params = {"symbol": symbol, "interval": "4h", "limit": limit}
    data = await _fetch(session, "/futures/data/openInterestHist", params=params)
    if not data:
        return np.asarray([], dtype=float)

    vals: List[float] = []
    for item in data:
        v = item.get("sumOpenInterest") or item.get("openInterest")
        try:
            vals.append(float(v))
        except Exception:
            continue

    return np.asarray(vals, dtype=float)


async def _fetch_open_interest_spot(session: aiohttp.ClientSession, symbol: str) -> float:
    """
    Fallback OI simple : /fapi/v1/openInterest (snapshot unique).
    """
    params = {"symbol": symbol}
    data = await _fetch(session, "/fapi/v1/openInterest", params=params)
    if not data:
        return 0.0
    try:
        return float(data.get("openInterest", 0.0))
    except Exception:
        return 0.0


async def _fetch_funding_rate(session: aiohttp.ClientSession, symbol: str) -> float:
    """
    Récupère la dernière funding rate (ou moyenne récente).
    Endpoint : /fapi/v1/fundingRate
    """
    params = {"symbol": symbol, "limit": 1}
    data = await _fetch(session, "/fapi/v1/fundingRate", params=params)
    if not data:
        return 0.0

    try:
        # data est une liste d'objets; on prend le dernier
        item = data[-1]
        return float(item.get("fundingRate", 0.0))
    except Exception:
        return 0.0


async def _fetch_klines_for_cvd(session: aiohttp.ClientSession, symbol: str, limit: int = 80) -> np.ndarray:
    """
    Utilise les futures klines pour approcher un CVD à partir :
      - volume total
      - takerBuyBaseVolume

    Endpoint : /fapi/v1/klines
    Chaque kline : [ ..., volume, quoteVolume, takerBuyBaseVolume, takerBuyQuoteVolume, ... ]
    """
    params = {"symbol": symbol, "interval": "1h", "limit": limit}
    data = await _fetch(session, "/fapi/v1/klines", params=params)
    if not data:
        return np.asarray([], dtype=float)

    deltas: List[float] = []

    for k in data:
        # positions 5 = volume, 9 = takerBuyBaseVolume
        try:
            vol = float(k[5])
            taker_buy = float(k[9])
            # Approx CVD delta = (aggressive_buy - aggressive_sell)
            # = (2 * taker_buy - volume)
            delta = 2.0 * taker_buy - vol
            deltas.append(delta)
        except Exception:
            continue

    if not deltas:
        return np.asarray([], dtype=float)

    cvd = np.cumsum(deltas)
    return cvd


async def _fetch_liquidations(session: aiohttp.ClientSession, symbol: str, limit: int = 100) -> Dict[str, float]:
    """
    Récupère les liquidations récentes via :
      /fapi/v1/forceOrders

    Agrège le volume des liquidations côté acheteurs / vendeurs.
    """
    params = {"symbol": symbol, "limit": limit}
    data = await _fetch(session, "/fapi/v1/forceOrders", params=params)
    if not data:
        return {"liq_buy": 0.0, "liq_sell": 0.0}

    buy = 0.0
    sell = 0.0

    for item in data:
        try:
            side = item.get("side", "").upper()
            qty = float(item.get("origQty") or item.get("executedQty") or 0.0)
        except Exception:
            continue

        if side == "BUY":
            buy += qty
        elif side == "SELL":
            sell += qty

    return {"liq_buy": buy, "liq_sell": sell}


# =====================================================================
# SCORE INSTITUTIONNEL GLOBAL
# =====================================================================

async def compute_full_institutional_analysis(symbol: str, bias: str) -> Dict[str, Any]:
    """
    Fonction appelée par analyze_signal.SignalAnalyzer.

    Objectif :
      - Récupérer OI, funding, CVD, liquidations sur Binance Futures (gratuit)
      - Construire un score institutionnel integer
      - Fournir les métriques brutes pour debug / logs
    """
    bias = (bias or "").upper()

    # Mapping simple Bitget → Binance : BTCUSDT reste BTCUSDT, AVAXUSDT etc.
    binance_symbol = symbol.replace("-", "")

    async with aiohttp.ClientSession() as session:
        oi_hist_task = _fetch_open_interest_hist(session, binance_symbol, limit=30)
        funding_task = _fetch_funding_rate(session, binance_symbol)
        cvd_task = _fetch_klines_for_cvd(session, binance_symbol, limit=80)
        liq_task = _fetch_liquidations(session, binance_symbol, limit=100)

        oi_hist, funding, cvd_series, liq = (
            await oi_hist_task,
            await funding_task,
            await cvd_task,
            await liq_task,
        )

    # OI slope
    if oi_hist is None or len(oi_hist) == 0:
        oi_slope = 0.0
    else:
        try:
            oi_slope = float(oi_hist[-1] - oi_hist[0]) / max(abs(oi_hist[0]), 1e-8)
        except Exception:
            oi_slope = 0.0

    # CVD slope
    if cvd_series is None or len(cvd_series) < 2:
        cvd_slope = 0.0
    else:
        try:
            cvd_slope = float(cvd_series[-1] - cvd_series[-10]) / max(abs(cvd_series[-10]), 1e-8)
        except Exception:
            cvd_slope = float(cvd_series[-1] - cvd_series[0]) / max(abs(cvd_series[0]), 1e-8)

    # Liquidations
    liq_buy = float(liq.get("liq_buy", 0.0)) if liq else 0.0
    liq_sell = float(liq.get("liq_sell", 0.0)) if liq else 0.0
    total_liq = liq_buy + liq_sell
    liq_pressure = 0.0
    if total_liq > 0:
        liq_pressure = (liq_buy - liq_sell) / total_liq  # >0 : plus de buy liq, <0 : plus de sell liq

    # Score institutionnel (0–4)
    score = 0

    if (bias == "LONG" and oi_slope > 0) or (bias == "SHORT" and oi_slope < 0):
        score += 1

    if (bias == "LONG" and cvd_slope > 0) or (bias == "SHORT" and cvd_slope < 0):
        score += 1

    if (bias == "LONG" and funding > 0) or (bias == "SHORT" and funding < 0):
        score += 1

    if (bias == "LONG" and liq_pressure < 0) or (bias == "SHORT" and liq_pressure > 0):
        score += 1

    score = int(max(0, min(4, score)))

    # Pression globale lissée
    try:
        pressure = (
            0.4 * float(np.tanh(oi_slope * 5.0))
            + 0.4 * float(np.tanh(cvd_slope * 5.0))
            + 0.2 * float(np.tanh(funding * 1000.0))
        )
    except Exception:
        pressure = 0.0

    return {
        "oi_slope": float(oi_slope),
        "cvd_slope": float(cvd_slope),
        "funding": float(funding),
        "liq_buy": liq_buy,
        "liq_sell": liq_sell,
        "liq_pressure": float(liq_pressure),
        "pressure": float(pressure),
        "institutional_score": int(score),
        "details": {
            "oi_trend": float(oi_slope),
            "cvd": float(cvd_slope),
            "funding": float(funding),
            "liq_pressure": float(liq_pressure),
        },
    }
