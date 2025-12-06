# ================================================================
# bitget_trader.py — Async order execution for Bitget Futures USDT-M
# ================================================================
import aiohttp
import asyncio
from typing import Optional, Dict, Any
from bitget_client import get_client


class BitgetTrader:
    """
    Trader institutionnel Bitget — version ASYNC.
    Fonctionnalités :
      - Placement LIMIT (entrée)
      - Stop Loss (reduce-only)
      - TP1, TP2 (reduce-only)
      - Annulation
      - Vérification position
      - Normalisation des tailles
    """

    def __init__(self, api_key: str, api_secret: str, api_passphrase: str):
        self.api_key = api_key
        self.api_secret = api_secret
        self.api_passphrase = api_passphrase

    async def _client(self):
        return await get_client(self.api_key, self.api_secret, self.api_passphrase)

    # ------------------------------------------------------------
    # Utils
    # ------------------------------------------------------------
    def _normalize_qty(self, qty: float, lot_size: float) -> float:
        """Arrondit la quantité au lotSize Bitget."""
        try:
            lot = float(lot_size)
            if lot <= 0:
                return qty
            steps = qty / lot
            steps = int(steps)
            return max(lot, steps * lot)
        except:
            return qty

    # ------------------------------------------------------------
    # Place LIMIT ENTRY
    # ------------------------------------------------------------
    async def place_limit(
        self,
        symbol: str,
        side: str,         # LONG / SHORT
        price: float,
        qty: float,
        margin_coin: str = "USDT",
        post_only: bool = True
    ) -> Dict[str, Any]:

        client = await self._client()
        contract = await client.get_contract(symbol)

        if not contract:
            return {"error": "Unknown symbol contract", "symbol": symbol}

        lot_size = float(contract.get("size", 0.001))
        qty = self._normalize_qty(qty, lot_size)

        body = {
            "symbol": symbol,
            "marginCoin": margin_coin,
            "size": str(qty),
            "price": str(price),
            "side": "open_long" if side.upper() == "LONG" else "open_short",
            "orderType": "limit",
        }

        if post_only:
            body["timeInForceValue"] = "post_only"

        r = await client._request("POST", "/api/mix/v1/order/placeOrder", data=body)
        return r

    # ------------------------------------------------------------
    # Place STOP LOSS reduce-only
    # ------------------------------------------------------------
    async def place_stop_loss(
        self,
        symbol: str,
        side: str,
        sl_price: float,
        qty: float,
        margin_coin: str = "USDT"
    ) -> Dict[str, Any]:

        client = await self._client()
        contract = await client.get_contract(symbol)
        if not contract:
            return {"error": "Unknown contract"}

        lot_size = float(contract.get("size", 0.001))
        qty = self._normalize_qty(qty, lot_size)

        # SL côté opposé
        trigger_side = "close_long" if side.upper() == "LONG" else "close_short"

        body = {
            "symbol": symbol,
            "marginCoin": margin_coin,
            "triggerPrice": str(sl_price),
            "executePrice": str(sl_price),
            "side": trigger_side,
            "size": str(qty),
            "orderType": "trigger",
        }

        r = await client._request("POST", "/api/mix/v1/order/placeOrder", data=body)
        return r

    # ------------------------------------------------------------
    # Place TP reduce-only (TP1 / TP2)
    # ------------------------------------------------------------
    async def place_take_profit(
        self,
        symbol: str,
        side: str,
        tp_price: float,
        qty: float,
        margin_coin: str = "USDT"
    ) -> Dict[str, Any]:

        client = await self._client()
        contract = await client.get_contract(symbol)
        if not contract:
            return {"error": "Unknown contract"}

        lot_size = float(contract.get("size", 0.001))
        qty = self._normalize_qty(qty, lot_size)

        trigger_side = "close_long" if side.upper() == "LONG" else "close_short"

        body = {
            "symbol": symbol,
            "marginCoin": margin_coin,
            "triggerPrice": str(tp_price),
            "executePrice": str(tp_price),
            "side": trigger_side,
            "size": str(qty),
            "orderType": "trigger",
        }

        r = await client._request("POST", "/api/mix/v1/order/placeOrder", data=body)
        return r

    # ------------------------------------------------------------
    # CANCEL ALL OPEN ORDERS
    # ------------------------------------------------------------
    async def cancel_all(self, symbol: str, margin_coin: str = "USDT") -> Dict[str, Any]:
        client = await self._client()
        body = {"symbol": symbol, "marginCoin": margin_coin}
        r = await client._request("POST", "/api/mix/v1/order/cancelAllOrders", data=body)
        return r

    # ------------------------------------------------------------
    # GET POSITION
    # ------------------------------------------------------------
    async def get_position(self, symbol: str) -> Dict[str, Any]:
        client = await self._client()
        pos = await client.get_position(symbol)
        return pos or {}

    # ------------------------------------------------------------
    # Modify SL / TP (reduce-only)
    # ------------------------------------------------------------
    async def move_stop_loss(
        self,
        symbol: str,
        side: str,
        new_sl: float,
        qty: float,
        margin_coin: str = "USDT"
    ):
        # Simple implémentation : on annule + replace
        await self.cancel_all(symbol, margin_coin)
        return await self.place_stop_loss(symbol, side, new_sl, qty, margin_coin)
