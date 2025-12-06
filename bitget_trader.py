# =====================================================================
# bitget_trader.py — Async order execution for Bitget Futures USDT-M
# Version corrigée, professionnelle et 100% compatible
# =====================================================================

from typing import Optional, Dict, Any
from bitget_client import get_client


class BitgetTrader:
    """
    Trader institutionnel Bitget — version ASYNC.
    Fonctionnalités :
      - Placement LIMIT (entrée)
      - Stop Loss (reduce-only)
      - TP1, TP2 (reduce-only)
      - Annulation d'ordre
      - Récup position
    """

    def __init__(self, api_key: str, api_secret: str, api_passphrase: str):
        self.api_key = api_key
        self.api_secret = api_secret
        self.api_passphrase = api_passphrase

    async def _client(self):
        return await get_client(self.api_key, self.api_secret, self.api_passphrase)

    # ------------------------------------------------------------
    # Utils — qty rounding
    # ------------------------------------------------------------
    def _normalize_qty(self, qty: float, lot_size: float) -> float:
        """Arrondit la quantité au lotSize Bitget."""
        try:
            lot = float(lot_size)
            if lot <= 0:
                return qty
            steps = int(qty / lot)
            final_qty = steps * lot
            return max(final_qty, lot)
        except:
            return qty

    # ------------------------------------------------------------
    # LIMIT ENTRY ORDER
    # ------------------------------------------------------------
    async def place_limit(
        self,
        symbol: str,
        side: str,    # LONG / SHORT
        price: float,
        qty: float,
        margin_coin: str = "USDT",
        post_only: bool = True
    ) -> Dict[str, Any]:

        client = await self._client()
        contract = await client.get_contract(symbol)

        if not contract:
            return {"error": "Unknown symbol contract"}

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

        return await client._request("POST", "/api/mix/v1/order/placeOrder", data=body)

    # ------------------------------------------------------------
    # STOP LOSS (TRIGGER ORDER)
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

        trigger_side = "close_long" if side.upper() == "LONG" else "close_short"

        body = {
            "symbol": symbol,
            "marginCoin": margin_coin,
            "triggerPrice": str(sl_price),
            "executePrice": str(sl_price),
            "orderType": "plan",
            "side": trigger_side,
            "size": str(qty),
            "triggerType": "fill_price",
        }

        return await client._request("POST", "/api/mix/v1/order/placePlan", data=body)

    # ------------------------------------------------------------
    # TAKE PROFIT (TRIGGER ORDER)
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
            "orderType": "profit",
            "side": trigger_side,
            "size": str(qty),
            "triggerType": "fill_price",
        }

        return await client._request("POST", "/api/mix/v1/order/placePlan", data=body)

    # ------------------------------------------------------------
    # Cancel all pending orders
    # ------------------------------------------------------------
    async def cancel_all(self, symbol: str, margin_coin: str = "USDT") -> Dict[str, Any]:
        client = await self._client()
        body = {"symbol": symbol, "marginCoin": margin_coin}
        return await client._request("POST", "/api/mix/v1/order/cancelAllOrders", data=body)

    # ------------------------------------------------------------
    # GET POSITION
    # ------------------------------------------------------------
    async def get_position(self, symbol: str) -> Dict[str, Any]:
        client = await self._client()
        pos = await client.get_position(symbol)
        return pos or {}

    # ------------------------------------------------------------
    # MOVE STOP LOSS (Cancel + re-place)
    # ------------------------------------------------------------
    async def move_stop_loss(
        self,
        symbol: str,
        side: str,
        new_sl: float,
        qty: float,
        margin_coin: str = "USDT"
    ):
        await self.cancel_all(symbol, margin_coin)
        return await self.place_stop_loss(symbol, side, new_sl, qty, margin_coin)
