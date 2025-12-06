# =====================================================================
# institutional_data.py — Desk Lead Binance v1.0
# Flux institutionnel temps réel (Open Interest, Funding, CVD, Liquidations)
# Compatible analyze_signal.py + structure_utils.py
# =====================================================================

from __future__ import annotations
import aiohttp
import numpy as np
from typing import Dict, Any, Optional, List


# =====================================================================
# Mapping Bitget → Binance Futures
# =====================================================================

def map_to_binance(symbol: str) -> Optional[str]:
    """
    Convertit un symbole Bitget (ex: BTCUSDT_UMCBL) en symbole Binance Futures (BTCUSDT).
    Règles :
        - enlever suffixes _UMCBL / _DMCBL
        - base intacte: XRPUSDT
    """
    if not symbol:
        return None

    s = symbol.upper().replace("_UMCBL", "").replace("_DMCBL", "")
    return s if s.endswith("USDT") else None


# =====================================================================
# Binance HTTP Client
# =====================================================================

class BinanceHTTP:
    BASE = "https://fapi.binance.com"

    @staticmethod
    async def get(session: aiohttp.ClientSession, endpoint: str, params=None):
        try:
            async with session.get(BinanceHTTP.BASE + endpoint, params=params, timeout=5) as r:
                if r.status != 200:
                    return {}
                return await r.json()
        except:
            return {}

    # -------------------------
    @staticmethod
    async def fetch_open_interest(session, symbol: str) -> Optional[float]:
        data = await BinanceHTTP.get(session, "/fapi/v1/openInterest", params={"symbol": symbol})
        try:
            return float(data.get("openInterest", 0))
        except:
            return None

    @staticmethod
    async def fetch_funding(session, symbol: str) -> Optional[float]:
        data = await BinanceHTTP.get(session, "/fapi/v1/premiumIndex", params={"symbol": symbol})
        try:
            return float(data.get("lastFundingRate", 0))
        except:
            return None

    # -------------------------
    @staticmethod
    async def fetch_agg_trades(session, symbol: str, limit: int = 200):
        data = await BinanceHTTP.get(
            session,
            "/fapi/v1/aggTrades",
            params={"symbol": symbol, "limit": limit},
        )
        return data if isinstance(data, list) else []


# =====================================================================
# CVD computation (desk-grade)
# =====================================================================

def compute_cvd_from_trades(trades: List[Dict[str, Any]]) -> float:
    """
    CVD = Σ (buy - sell) approximé via flag 'm':
        m = True  => maker = SELL aggressor
        m = False => BUY aggressor
    """
    buy, sell = 0.0, 0.0
    try:
        for t in trades:
            qty = float(t.get("q", 0.0))
            maker = t.get("m", False)
            if maker:
                sell += qty
            else:
                buy += qty
    except:
        pass
    return buy - sell


# =====================================================================
# Liquidations (strong institutional signal)
# =====================================================================

async def fetch_liquidations(session, symbol: str, limit: int = 100) -> float:
    """
    Approx liquidation volume aggregator (public Binance liquidation stream unavailable REST).
    Trick :
        - utilise aggTrades large qty > threshold comme proxy
    """
    data = await BinanceHTTP.get(
        session,
        "/fapi/v1/aggTrades",
        params={"symbol": symbol, "limit": limit},
    )

    if not isinstance(data, list):
        return 0.0

    # threshold dynamique — repère agressions massives
    vols = []
    for t in data:
        try:
            qty = float(t.get("q", 0.0))
            vols.append(qty)
        except:
            continue

    if not vols:
        return 0.0

    avg = np.mean(vols)
    large = [v for v in vols if v > avg * 3.0]
    return float(sum(large))


# =====================================================================
# SCORE INSTITUTIONNEL — Desk Lead Standard
# =====================================================================

class InstitutionalData:
    def __init__(self, api_key: str, api_secret: str, api_passphrase: str):
        # On ne les utilise pas pour Binance (public API)
        self.api_key = api_key
        self.api_secret = api_secret
        self.api_passphrase = api_passphrase

    # ------------------------------------------------------------
    async def compute_score(self, symbol: str) -> Dict[str, Any]:
        """
        Retourne :
            {
              score: 0..4,
              oi: float,
              funding: float,
              cvd: float,
              liq: float,
              details: {...}
            }
        """
        b_symbol = map_to_binance(symbol)
        if not b_symbol:
            return {"score": 0, "error": "unmappable_symbol"}

        async with aiohttp.ClientSession() as session:
            oi = await BinanceHTTP.fetch_open_interest(session, b_symbol)
            funding = await BinanceHTTP.fetch_funding(session, b_symbol)

            trades = await BinanceHTTP.fetch_agg_trades(session, b_symbol, limit=200)
            cvd = compute_cvd_from_trades(trades)

            liq = await fetch_liquidations(session, b_symbol)

        # -------------------------
        # Scoring institutionnel
        # -------------------------

        score = 0
        det = {}

        # OI expansion
        if oi and oi > 0:
            det["oi"] = oi
            score += 1

        # Funding aligné
        if funding is not None:
            det["funding"] = funding
            if abs(funding) > 0.0001:
                score += 1

        # CVD dominance
        det["cvd"] = cvd
        if abs(cvd) > 0:
            score += 1

        # Liquidations présentes
        det["liq"] = liq
        if liq > 0:
            score += 1

        return {
            "symbol": symbol,
            "binance_symbol": b_symbol,
            "oi": oi,
            "funding": funding,
            "cvd": cvd,
            "liq": liq,
            "score": float(score),
            "details": det,
        }
