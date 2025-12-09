# =====================================================================
# bitget_trader.py — Desk Lead Execution Engine (2025)
# =====================================================================
# Exécute :
#   ✔ LIMIT ORDERS
#   ✔ STOP-LOSS (trigger)
#   ✔ TAKE PROFIT (trigger)
#   ✔ Qty basé sur USDT
#
# API Bitget v2 (2025) — entièrement compatible scanner/analyze_signal
# =====================================================================

import time
import hmac
import base64
import hashlib
import json
import asyncio
from typing import Optional, Dict, Any

from bitget_client import get_client, map_symbol_kucoin_to_bitget


class BitgetTrader:

    def __init__(self, api_key: str, api_secret: str, api_passphrase: str):
        self.api_key = api_key
        self.api_secret = api_secret.encode()
        self.api_passphrase = api_passphrase

    # ------------------------------------------------------------
    # SIGNATURE
    # ------------------------------------------------------------
    def _sign(self, ts: str, method: str, path: str, query: str, body: str) -> str:
        msg = f"{ts}{method}{path}{query}{body}"
        mac = hmac.new(self.api_secret, msg.encode(), hashlib.sha256).digest()
        return base64.b64encode(mac).decode()

    # ------------------------------------------------------------
    async def _request(self, method: str, path: str, *, params=None, data=None) -> Dict[str, Any]:
        """
        Passe par bitget_client rate-limited + retry
        """
        client = await get_client(self.api_key, self.api_secret.decode(), self.api_passphrase)

        # On passe directement via la session/signature du client parent
        return await client._request(method, path, params=params, data=data, auth=True)

    # ------------------------------------------------------------
    # PLACE LIMIT ORDER
    # ------------------------------------------------------------
    async def place_limit(self, symbol: str, side: str, price: float, qty: float):
        """
        ORDER LIMIT Bitget v2
        """
        mapped = map_symbol_kucoin_to_bitget(symbol)
        if not mapped:
            return {"ok": False, "msg": "Symbol mapping failed"}

        side_final = "buy" if side.lower() == "long" else "sell"

        data = {
            "symbol": mapped,
            "marginCoin": "USDT",
            "size": str(qty),
            "price": str(price),
            "orderType": "limit",
            "side": side_final
        }

        return await self._request(
            "POST",
            "/api/v2/mix/order/place-order",
            data=data
        )

    # ------------------------------------------------------------
    # STOP LOSS
    # ------------------------------------------------------------
    async def place_stop_loss(self, symbol: str, side: str, sl: float, qty: float):
        mapped = map_symbol_kucoin_to_bitget(symbol)
        if not mapped:
            return {"ok": False, "msg": "Symbol mapping failed"}

        trigger_price = sl
        side_final = "sell" if side.lower() == "long" else "buy"

        data = {
            "symbol": mapped,
            "marginCoin": "USDT",
            "size": str(qty),
            "triggerPrice": str(trigger_price),
            "triggerType": "mark_price",
            "executePrice": str(trigger_price),
            "orderType": "limit",
            "side": side_final
        }

        return await self._request(
            "POST",
            "/api/v2/mix/order/place-plan-order",
            data=data
        )

    # ------------------------------------------------------------
    # TAKE PROFIT
    # ------------------------------------------------------------
    async def place_take_profit(self, symbol: str, side: str, tp: float, qty: float):
        mapped = map_symbol_kucoin_to_bitget(symbol)
        if not mapped:
            return {"ok": False, "msg": "Symbol mapping failed"}

        trigger_price = tp
        side_final = "sell" if side.lower() == "long" else "buy"

        data = {
            "symbol": mapped,
            "marginCoin": "USDT",
            "size": str(qty),
            "triggerPrice": str(trigger_price),
            "triggerType": "mark_price",
            "executePrice": str(trigger_price),
            "orderType": "limit",
            "side": side_final
        }

        return await self._request(
            "POST",
            "/api/v2/mix/order/place-plan-order",
            data=data
        )
