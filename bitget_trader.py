# =====================================================================
# bitget_trader.py — Desk Lead Execution Engine (2025)
#
# Full Bitget Futures USDT-M:
#   ✔ place_limit()
#   ✔ place_market()
#   ✔ place_stop_loss()
#   ✔ place_take_profit()
#   ✔ Auto-normalisation qty & price ticks
#   ✔ Compatible Bitget v2 API
# =====================================================================

from __future__ import annotations

import time
import asyncio
import json
import logging

from typing import Dict, Any, Optional
from bitget_client import get_client

LOGGER = logging.getLogger(__name__)


# =====================================================================
# Helpers
# =====================================================================

def _round_tick(price: float, step: float) -> float:
    return round(price / step) * step


def _round_qty(qty: float, step: float) -> float:
    return round(qty / step) * step


# =====================================================================
# Trader Class
# =====================================================================

class BitgetTrader:
    def __init__(self, api_key: str, api_secret: str, api_passphrase: str):
        self.api_key = api_key
        self.api_secret = api_secret
        self.api_passphrase = api_passphrase

        self.client = None

    # ------------------------------------------------------------
    async def _ensure_client(self):
        if self.client is None:
            self.client = await get_client(self.api_key, self.api_secret, self.api_passphrase)

    # ------------------------------------------------------------
    async def _normalize_order_params(self, symbol: str, price: float, qty: float):
        """
        Normalise price + qty using contract metadata.
        """
        await self._ensure_client()

        contract = await self.client.get_contract(symbol)
        if not contract:
            LOGGER.error(f"No contract metadata for {symbol}")
            return price, qty

        price_step = float(contract.get("priceEndStep", "1"))
        vol_step = float(contract.get("sizeMultiplier", "0.001"))

        price = _round_tick(price, price_step)
        qty = max(_round_qty(qty, vol_step), vol_step)

        return price, qty

    # =====================================================================
    # === PLACE LIMIT ORDER ===
    # =====================================================================

    async def place_limit(self, symbol: str, side: str, price: float, qty: float) -> Dict[str, Any]:
        """
        side = "buy" or "sell"
        """
        await self._ensure_client()

        price, qty = await self._normalize_order_params(symbol, price, qty)

        payload = {
            "symbol": symbol,
            "marginCoin": "USDT",
            "orderType": "limit",
            "price": str(price),
            "size": str(qty),
            "side": "open_long" if side == "BUY" else "open_short",
        }

        r = await self.client._request(
            "POST", "/api/v2/mix/order/placeOrder", data=payload
        )

        if not r.get("ok"):
            LOGGER.error(f"[ORDER FAIL] {symbol} {side} LIMIT error: {r}")
        else:
            LOGGER.info(f"[ORDER OK] {symbol} LIMIT {side} {qty} @ {price}")

        return r

    # =====================================================================
    # === MARKET ORDER ===
    # =====================================================================

    async def place_market(self, symbol: str, side: str, qty: float):
        await self._ensure_client()

        _, qty = await self._normalize_order_params(symbol, 0, qty)

        payload = {
            "symbol": symbol,
            "marginCoin": "USDT",
            "orderType": "market",
            "size": str(qty),
            "side": "open_long" if side == "BUY" else "open_short",
        }

        return await self.client._request(
            "POST", "/api/v2/mix/order/placeOrder", data=payload
        )

    # =====================================================================
    # === STOP LOSS ===
    # =====================================================================

    async def place_stop_loss(self, symbol: str, side: str, sl_price: float, qty: float):
        await self._ensure_client()

        sl_price, qty = await self._normalize_order_params(symbol, sl_price, qty)

        payload = {
            "symbol": symbol,
            "marginCoin": "USDT",
            "triggerType": "mark_price",
            "triggerPrice": str(sl_price),
            "executePrice": str(sl_price),
            "size": str(qty),
            "orderType": "limit",
            "side": "close_long" if side == "BUY" else "close_short",
        }

        r = await self.client._request(
            "POST", "/api/v2/mix/order/placePlanOrder", data=payload
        )

        if not r.get("ok"):
            LOGGER.error(f"[SL FAIL] {symbol} SL {side}: {r}")
        else:
            LOGGER.info(f"[SL OK] {symbol} SL {side} @ {sl_price}")

        return r

    # =====================================================================
    # === TAKE PROFIT ===
    # =====================================================================

    async def place_take_profit(self, symbol: str, side: str, tp_price: float, qty: float):
        await self._ensure_client()

        tp_price, qty = await self._normalize_order_params(symbol, tp_price, qty)

        payload = {
            "symbol": symbol,
            "marginCoin": "USDT",
            "triggerType": "mark_price",
            "triggerPrice": str(tp_price),
            "executePrice": str(tp_price),
            "size": str(qty),
            "orderType": "limit",
            "side": "close_long" if side == "BUY" else "close_short",
        }

        r = await self.client._request(
            "POST", "/api/v2/mix/order/placePlanOrder", data=payload
        )

        if not r.get("ok"):
            LOGGER.error(f"[TP FAIL] {symbol} TP {side}: {r}")
        else:
            LOGGER.info(f"[TP OK] {symbol} TP {side} @ {tp_price}")

        return r
