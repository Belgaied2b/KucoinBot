# =====================================================================
# institutional_data.py — Metrics institutionnels Bitget + CVD Binance
# =====================================================================
import aiohttp
import asyncio
from typing import Dict, Optional, Any
from bitget_client import get_client
import time
import numpy as np


BINANCE_BASE = "https://fapi.binance.com"  # Pour le CVD uniquement


class InstitutionalData:
    """
    Fournit les données institutionnelles pour un symbole :
        - Open Interest (Bitget)
        - Funding Rate (Bitget)
        - Long/Short Ratio (Bitget)
        - Liquidations (Bitget)
        - CVD (Binance fallback)
        - Score institutionnel global (0 → 3)
    """

    def __init__(self, api_key: str, api_secret: str, api_passphrase: str):
        self.api_key = api_key
        self.api_secret = api_secret
        self.api_passphrase = api_passphrase

    async def _bitget(self):
        return await get_client(self.api_key, self.api_secret, self.api_passphrase)

    # ----------------------------------------------------------------------
    # BITGET — OPEN INTEREST
    # ----------------------------------------------------------------------
    async def get_oi(self, symbol: str) -> Optional[float]:
        client = await self._bitget()
        return await client.get_open_interest(symbol)

    # ----------------------------------------------------------------------
    # BITGET — FUNDING RATE
    # ----------------------------------------------------------------------
    async def get_funding(self, symbol: str) -> Optional[float]:
        client = await self._bitget()
        return await client.get_funding_rate(symbol)

    # ----------------------------------------------------------------------
    # BITGET — LONG/SHORT RATIO
    # ----------------------------------------------------------------------
    async def get_long_short_ratio(self, symbol: str) -> Optional[float]:
        """
        Ratio long/short traders Bitget.
        Retourne :
            >1 bullish
            <1 bearish
        """
        path = "/api/mix/v1/market/takerBuySellRatio"
        client = await self._bitget()
        r = await client._request("GET", path, params={"symbol": symbol}, auth=False)
        try:
            data = r.get("data", [])
            if not data:
                return None
            return float(data[-1].get("takerBuySellRatio", 1))
        except:
            return None

    # ----------------------------------------------------------------------
    # BITGET — LIQUIDATIONS (simplifié)
    # ----------------------------------------------------------------------
    async def get_liquidations(self, symbol: str) -> Dict[str, float]:
        """
        Liquidations approximées via 'markPriceKlines' Bitget.
        On mesure juste la taille des mèches vs corps.
        Retour :
            {"bull": score, "bear": score}
        """

        client = await self._bitget()

        r = await client._request(
            "GET",
            "/api/mix/v1/market/markPriceCandles",
            params={"symbol": symbol, "granularity": "1h", "limit": 30},
            auth=False,
        )
        data = r.get("data", [])
        if not data:
            return {"bull": 0.0, "bear": 0.0}

        bull_lq = 0
        bear_lq = 0

        for k in data[-20:]:
            # Format Bitget: [timestamp, open, high, low, close]
            _, o, h, l, c = float(k[0]), float(k[1]), float(k[2]), float(k[3]), float(k[4])
            body = abs(c - o)
            wick_up = h - max(o, c)
            wick_down = min(o, c) - l

            if wick_up > body * 2:
                bear_lq += wick_up
            if wick_down > body * 2:
                bull_lq += wick_down

        # Normalisation simple
        scale = max(bull_lq + bear_lq, 1e-12)
        return {
            "bull": float(bull_lq / scale),
            "bear": float(bear_lq / scale),
        }

    # ----------------------------------------------------------------------
    # BINANCE — CVD via aggTrades
    # ----------------------------------------------------------------------
    async def get_cvd(self, symbol: str) -> Optional[float]:
        """
        CVD approximatif via aggTrades Binance.
        symbol Bitget ex: BTCUSDT → Binance : BTCUSDT
        """
        binance_symbol = symbol.replace("_UMCBL", "")  # Ex: BTCUSDT_UMCBL

        url = f"{BINANCE_BASE}/fapi/v1/aggTrades"
        params = {"symbol": binance_symbol, "limit": 500}

        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(url, params=params, timeout=10) as resp:
                    if resp.status != 200:
                        return None
                    trades = await resp.json()
        except:
            return None

        if not trades:
            return None

        cvd = 0
        for t in trades:
            qty = float(t["q"])
            is_buy = t["m"]  # Binance: m=True → seller is maker → selling pressure
            if is_buy:
                cvd -= qty
            else:
                cvd += qty

        return cvd

    # ----------------------------------------------------------------------
    # SCORE INSTITUTIONNEL
    # ----------------------------------------------------------------------
    async def compute_score(self, symbol: str) -> Dict[str, Any]:
        """
        Score institutionnel global basé sur :
            - direction OI
            - funding bias
            - long/short ratio
            - liquidations
            - CVD Binance
        Score final = 0 → 3.
        """

        oi = await self.get_oi(symbol)
        funding = await self.get_funding(symbol)
        ratio = await self.get_long_short_ratio(symbol)
        liq = await self.get_liquidations(symbol)
        cvd = await self.get_cvd(symbol)

        # -------- PROCESSING --------
        score = 0
        details = {}

        # Open Interest (trend)
        if oi is not None:
            details["oi"] = oi
            if oi > 0:
                score += 1

        # Funding rate
        if funding is not None:
            details["funding"] = funding
            if funding > 0:
                score += 1

        # Long/Short ratio
        if ratio is not None:
            details["ratio"] = ratio
            if ratio > 1.0:
                score += 0.5

        # Liquidations
        if liq:
            bull = liq["bull"]
            bear = liq["bear"]
            details["liquidations"] = liq
            if bull > bear:
                score += 0.25
            elif bear > bull:
                score += 0.25

        # CVD (Binance)
        if cvd is not None:
            details["cvd"] = cvd
            if cvd > 0:
                score += 1
            else:
                score += 0

        # Clamp
        score = min(3.0, max(0.0, score))

        return {
            "score": score,
            "oi": oi,
            "funding": funding,
            "ratio": ratio,
            "cvd": cvd,
            "liq": liq,
            "details": details,
        }
