# =====================================================================
# institutional_data.py — Institutional Intelligence Engine ++
# Desk Lead++ version — OI / Funding / CVD / Liquidations / Long-Short
# Data source: Binance USDⓈ-M Futures (public, free)
# Fully compatible with analyze_signal.py (expects "institutional_score")
# =====================================================================

from typing import Dict, Any, Optional, List

import aiohttp
import numpy as np

BINANCE_FUTURES = "https://fapi.binance.com"


# =====================================================================
# HTTP helper
# =====================================================================

async def _fetch_json(
    session: aiohttp.ClientSession,
    path: str,
    params: Optional[dict] = None,
) -> Optional[Any]:
    """
    Light HTTP GET helper on Binance Futures REST.

    - Returns parsed JSON on success.
    - Returns None on any network / parsing error.
    """
    url = BINANCE_FUTURES + path
    try:
        async with session.get(url, params=params, timeout=5) as resp:
            if resp.status != 200:
                return None
            return await resp.json()
    except Exception:
        return None


def _to_float(v: Any, default: float = 0.0) -> float:
    try:
        f = float(v)
        if not np.isfinite(f):
            return default
        return f
    except Exception:
        return default


# =====================================================================
# Data fetchers
# =====================================================================

async def _fetch_open_interest_hist(
    session: aiohttp.ClientSession,
    symbol: str,
    period: str = "4h",
    limit: int = 30,
) -> np.ndarray:
    """
    Open Interest Statistics (history) :
      GET /futures/data/openInterestHist

    For USDⓈ-M Futures we use:
      - symbol = "BTCUSDT", "AVAXUSDT", etc.
      - period = "4h" by default.
    """
    params = {
        "symbol": symbol,
        "period": period,
        "limit": limit,
    }
    data = await _fetch_json(session, "/futures/data/openInterestHist", params=params)
    if not data or not isinstance(data, list):
        return np.asarray([], dtype=float)

    vals: List[float] = []
    for item in data:
        # For USDⓈ-M: field is "sumOpenInterest"
        v = item.get("sumOpenInterest")
        vals.append(_to_float(v, default=0.0))

    return np.asarray(vals, dtype=float)


async def _fetch_open_interest_snapshot(
    session: aiohttp.ClientSession,
    symbol: str,
) -> float:
    """
    Current Open Interest snapshot :
      GET /fapi/v1/openInterest
    """
    params = {"symbol": symbol}
    data = await _fetch_json(session, "/fapi/v1/openInterest", params=params)
    if not data or not isinstance(data, dict):
        return 0.0
    return _to_float(data.get("openInterest"), default=0.0)


async def _fetch_funding_rates(
    session: aiohttp.ClientSession,
    symbol: str,
    limit: int = 16,
) -> List[float]:
    """
    Funding Rate History :
      GET /fapi/v1/fundingRate

    - We fetch the last `limit` items (~several days).
    - Returns a list of floats (can be empty).
    """
    params = {"symbol": symbol, "limit": limit}
    data = await _fetch_json(session, "/fapi/v1/fundingRate", params=params)
    if not data or not isinstance(data, list):
        return []

    out: List[float] = []
    for item in data:
        out.append(_to_float(item.get("fundingRate"), default=0.0))
    return out


async def _fetch_klines_for_cvd_and_taker(
    session: aiohttp.ClientSession,
    symbol: str,
    interval: str = "1h",
    limit: int = 120,
) -> Dict[str, Any]:
    """
    Futures Klines :
      GET /fapi/v1/klines

    We derive:
      - CVD (Cumulative Volume Delta) approx using:
          delta = (2 * takerBuyBaseVolume - volume)
      - taker buy ratio = sum(takerBuyBaseVolume) / sum(volume)
      - simple price slope on close

    Returns dict with:
      {
        "cvd": np.ndarray,
        "taker_buy_ratio": float,
        "price_slope": float,
      }
    """
    params = {"symbol": symbol, "interval": interval, "limit": limit}
    data = await _fetch_json(session, "/fapi/v1/klines", params=params)
    if not data or not isinstance(data, list):
        return {"cvd": np.asarray([], dtype=float), "taker_buy_ratio": 0.0, "price_slope": 0.0}

    volumes: List[float] = []
    taker_buy: List[float] = []
    closes: List[float] = []
    deltas: List[float] = []

    for k in data:
        # kline payload:
        # [0] openTime, [1] open, [2] high, [3] low, [4] close,
        # [5] volume, [6] closeTime, [7] quoteVolume,
        # [8] trades, [9] takerBuyBase, [10] takerBuyQuote, [11] ignore
        v = _to_float(k[5], default=0.0)
        tb = _to_float(k[9], default=0.0)
        c = _to_float(k[4], default=0.0)

        volumes.append(v)
        taker_buy.append(tb)
        closes.append(c)

        delta = 2.0 * tb - v
        deltas.append(delta)

    if not volumes or not closes:
        return {"cvd": np.asarray([], dtype=float), "taker_buy_ratio": 0.0, "price_slope": 0.0}

    vol_arr = np.asarray(volumes, dtype=float)
    tb_arr = np.asarray(taker_buy, dtype=float)
    deltas_arr = np.asarray(deltas, dtype=float)
    closes_arr = np.asarray(closes, dtype=float)

    cvd = np.cumsum(deltas_arr)

    total_vol = float(np.sum(vol_arr))
    if total_vol <= 0:
        taker_buy_ratio = 0.5
    else:
        taker_buy_ratio = float(np.sum(tb_arr) / total_vol)

    # Price slope over the last ~20 bars
    if closes_arr.shape[0] >= 20:
        p_start = closes_arr[-20]
    else:
        p_start = closes_arr[0]
    p_end = closes_arr[-1]
    if p_start <= 0:
        price_slope = 0.0
    else:
        price_slope = (p_end - p_start) / p_start

    return {
        "cvd": cvd,
        "taker_buy_ratio": taker_buy_ratio,
        "price_slope": price_slope,
    }


async def _fetch_liquidations(
    session: aiohttp.ClientSession,
    symbol: str,
    limit: int = 100,
) -> Dict[str, float]:
    """
    Liquidations :
      GET /fapi/v1/forceOrders

    Aggregates BUY vs SELL liquidation volumes.
    """
    params = {"symbol": symbol, "limit": limit}
    data = await _fetch_json(session, "/fapi/v1/forceOrders", params=params)
    if not data or not isinstance(data, list):
        return {"liq_buy": 0.0, "liq_sell": 0.0}

    buy = 0.0
    sell = 0.0

    for item in data:
        side = str(item.get("side", "")).upper()
        qty = _to_float(item.get("origQty") or item.get("executedQty"), default=0.0)
        if side == "BUY":
            buy += qty
        elif side == "SELL":
            sell += qty

    return {"liq_buy": buy, "liq_sell": sell}


async def _fetch_global_long_short_ratio(
    session: aiohttp.ClientSession,
    symbol: str,
    period: str = "4h",
    limit: int = 30,
) -> Dict[str, float]:
    """
    Long/Short Ratio (MARKET_DATA)
      GET /futures/data/globalLongShortAccountRatio

    Returns dict with:
      {
        "long_short_ratio": float or 1.0,
        "long_account": float (0–1),
        "short_account": float (0–1),
      }
    or neutral defaults if any issue.
    """
    params = {"symbol": symbol, "period": period, "limit": limit}
    data = await _fetch_json(session, "/futures/data/globalLongShortAccountRatio", params=params)
    if not data or not isinstance(data, list):
        return {
            "long_short_ratio": 1.0,
            "long_account": 0.5,
            "short_account": 0.5,
        }

    last = data[-1]
    lsr = _to_float(last.get("longShortRatio"), default=1.0)
    long_acc = _to_float(last.get("longAccount"), default=0.5)
    short_acc = _to_float(last.get("shortAccount"), default=0.5)

    # In docs longAccount/shortAccount are 0–1, sometimes % (0–100). Normalize.
    if long_acc > 1.0 or short_acc > 1.0:
        long_acc /= 100.0
        short_acc /= 100.0

    # Clamp
    long_acc = min(max(long_acc, 0.0), 1.0)
    short_acc = min(max(short_acc, 0.0), 1.0)

    return {
        "long_short_ratio": lsr,
        "long_account": long_acc,
        "short_account": short_acc,
    }


# =====================================================================
# High-level institutional analysis
# =====================================================================

async def compute_full_institutional_analysis(symbol: str, bias: str) -> Dict[str, Any]:
    """
    Main entrypoint used by analyze_signal.SignalAnalyzer.

    INPUT:
      - symbol : "BTCUSDT", "AVAXUSDT", etc. (Bitget symbol mapped 1:1)
      - bias   : "LONG" or "SHORT" (direction of planned trade)

    RETURNS:
      - dict with:
          - institutional_score (0–4)   <-- used by analyze_signal
          - directional_score (0–3)
          - crowding_score (−1..+1)
          - flow_regime / crowding_regime / directional_bias
          - raw metrics: oi_slope, cvd_slope, funding stats, liq stats, taker_buy_ratio, etc.
    """
    bias = (bias or "").upper()
    if bias not in ("LONG", "SHORT"):
        bias = "LONG"

    # Mapping Bitget → Binance : "BTCUSDT" / "BTC-USDT" → "BTCUSDT"
    binance_symbol = symbol.replace("-", "")

    async with aiohttp.ClientSession() as session:
        # Fetch everything sequentially to avoid rate-limit spikes.
        oi_hist = await _fetch_open_interest_hist(session, binance_symbol, period="4h", limit=30)
        oi_snap = await _fetch_open_interest_snapshot(session, binance_symbol)
        funding_hist = await _fetch_funding_rates(session, binance_symbol, limit=16)
        kline_data = await _fetch_klines_for_cvd_and_taker(session, binance_symbol, interval="1h", limit=120)
        liq_data = await _fetch_liquidations(session, binance_symbol, limit=100)
        lsr_data = await _fetch_global_long_short_ratio(session, binance_symbol, period="4h", limit=30)

    # ----------------------------
    # Derived metrics
    # ----------------------------
    # 1) OI slope
    if oi_hist is None or oi_hist.size < 2:
        oi_slope = 0.0
    else:
        first_oi = float(oi_hist[0])
        last_oi = float(oi_hist[-1])
        if abs(first_oi) <= 1e-12:
            oi_slope = 0.0
        else:
            oi_slope = (last_oi - first_oi) / abs(first_oi)

    # 2) Funding stats
    if funding_hist:
        funding_last = float(funding_hist[-1])
        funding_mean = float(np.mean(funding_hist))
        funding_max_abs = float(np.max(np.abs(funding_hist)))
    else:
        funding_last = 0.0
        funding_mean = 0.0
        funding_max_abs = 0.0

    # 3) CVD / taker / price slope
    cvd = kline_data.get("cvd") if isinstance(kline_data, dict) else None
    if cvd is None or not isinstance(cvd, np.ndarray) or cvd.size < 2:
        cvd_slope = 0.0
        cvd_last = 0.0
    else:
        if abs(cvd[0]) <= 1e-12:
            cvd_slope = 0.0
        else:
            cvd_slope = float(cvd[-1] - cvd[0]) / max(abs(float(cvd[0])), 1e-8)
        cvd_last = float(cvd[-1])

    taker_buy_ratio = float(kline_data.get("taker_buy_ratio", 0.5))
    price_slope = float(kline_data.get("price_slope", 0.0))

    # 4) Liquidations
    liq_buy = _to_float(liq_data.get("liq_buy") if liq_data else 0.0, 0.0)
    liq_sell = _to_float(liq_data.get("liq_sell") if liq_data else 0.0, 0.0)
    liq_total = liq_buy + liq_sell
    if liq_total <= 0:
        liq_pressure = 0.0
    else:
        liq_pressure = (liq_buy - liq_sell) / liq_total  # >0 : plus de BUY liq (short squeeze), <0 : plus de SELL liq (long puke)

    # 5) Global long/short account ratio
    long_short_ratio = float(lsr_data.get("long_short_ratio", 1.0))
    long_account = float(lsr_data.get("long_account", 0.5))
    short_account = float(lsr_data.get("short_account", 0.5))

    # ----------------------------
    # Scores
    # ----------------------------
    directional_score = 0

    # OI directionnel
    if bias == "LONG" and oi_slope > 0:
        directional_score += 1
    elif bias == "SHORT" and oi_slope < 0:
        directional_score += 1

    # CVD directionnel
    if bias == "LONG" and cvd_slope > 0:
        directional_score += 1
    elif bias == "SHORT" and cvd_slope < 0:
        directional_score += 1

    # Taker buy/sell imbalance
    # taker_buy_ratio > 0.5 => flux acheteur agressif
    if bias == "LONG" and taker_buy_ratio > 0.52:
        directional_score += 1
    elif bias == "SHORT" and taker_buy_ratio < 0.48:
        directional_score += 1

    # Clamp [0,3]
    directional_score = int(max(0, min(3, directional_score)))

    # Directional bias label
    if directional_score >= 2:
        directional_bias = "FOR_BIAS"
    elif directional_score == 0:
        directional_bias = "AGAINST_BIAS"
    else:
        directional_bias = "MIXED"

    # Crowding via funding + long/short ratio
    crowding_score = 0
    crowding_regime = "NEUTRAL"

    funding_abs = abs(funding_last)

    # Seuils funding : 0.025% et 0.075% / 8h (valeurs typiques)
    mid_thr = 0.00025
    high_thr = 0.00075

    if funding_abs > mid_thr:
        if funding_last > 0 and long_short_ratio > 1.2:
            crowding_regime = "CROWDED_LONG"
            if bias == "LONG":
                crowding_score -= 1  # trop de monde déjà long
            elif bias == "SHORT":
                crowding_score += 1  # crowding en face
        elif funding_last < 0 and long_short_ratio < 0.8:
            crowding_regime = "CROWDED_SHORT"
            if bias == "SHORT":
                crowding_score -= 1
            elif bias == "LONG":
                crowding_score += 1

    # Clamp crowding_score in [-1, +1]
    crowding_score = int(max(-1, min(1, crowding_score)))

    # Liquidation intensity : on ne le signe pas, juste intensité globale
    if liq_total <= 0:
        liquidation_intensity = 0.0
    else:
        liquidation_intensity = float(abs(liq_pressure) * np.log1p(liq_total))

    # Flow regime simple : basé sur OI + CVD vs bias
    if directional_score >= 2 and price_slope * (1 if bias == "LONG" else -1) > 0:
        flow_regime = "SUPPORTING"
    elif directional_score == 0 and price_slope * (1 if bias == "LONG" else -1) > 0:
        flow_regime = "FADING"
    else:
        flow_regime = "MIXED"

    # ----------------------------
    # Final institutional_score (0–4) — backward compatible
    # ----------------------------
    score = directional_score  # base 0–3

    # Funding dans le sens du trade ajoute un point si pas trop extrême
    if (bias == "LONG" and funding_last > 0) or (bias == "SHORT" and funding_last < 0):
        score += 1

    # Si crowding "contre nous", on enlève 1
    if crowding_score < 0:
        score -= 1

    # On utilise aussi la liquidation : si énorme stress, on limite la confiance
    if liquidation_intensity > 3.0 and score > 0:
        score -= 1

    score = int(max(0, min(4, score)))

    # Pression agrégée (float, pour logs / debug)
    try:
        pressure = (
            0.4 * float(np.tanh(oi_slope * 5.0)) +
            0.4 * float(np.tanh(cvd_slope * 5.0)) +
            0.2 * float(np.tanh(funding_last * 2000.0))
        )
    except Exception:
        pressure = 0.0

    return {
        # principaux
        "institutional_score": int(score),
        "directional_score": int(directional_score),
        "crowding_score": int(crowding_score),
        "directional_bias": directional_bias,
        "crowding_regime": crowding_regime,
        "flow_regime": flow_regime,

        # métriques de base
        "oi_slope": float(oi_slope),
        "oi_current": float(oi_snap),
        "funding_last": float(funding_last),
        "funding_mean": float(funding_mean),
        "funding_max_abs": float(funding_max_abs),
        "cvd_slope": float(cvd_slope),
        "cvd_last": float(cvd_last),
        "taker_buy_ratio": float(taker_buy_ratio),
        "price_slope": float(price_slope),

        "liq_buy": float(liq_buy),
        "liq_sell": float(liq_sell),
        "liq_pressure": float(liq_pressure),
        "liquidation_intensity": float(liquidation_intensity),

        "long_short_ratio": float(long_short_ratio),
        "long_account": float(long_account),
        "short_account": float(short_account),

        "pressure": float(pressure),

        # détails pour debug / logs avancés
        "details": {
            "oi_trend": float(oi_slope),
            "cvd": float(cvd_slope),
            "funding_last": float(funding_last),
            "funding_mean": float(funding_mean),
            "funding_max_abs": float(funding_max_abs),
            "liq_pressure": float(liq_pressure),
            "liq_total": float(liq_total),
            "taker_buy_ratio": float(taker_buy_ratio),
            "price_slope": float(price_slope),
            "directional_bias": directional_bias,
            "crowding_regime": crowding_regime,
            "flow_regime": flow_regime,
        },
    }
