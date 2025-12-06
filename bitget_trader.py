# =====================================================================
# bitget_trader.py — Desk Lead Execution Layer (Bitget Futures USDT-M)
# Exécution institutionnelle complète : LIMIT, SL/TP, partials, trailing.
# Compatible: bitget_client.py (Desk Lead), scanner/analyzer, sizing, risk.
# =====================================================================

from __future__ import annotations
import asyncio
import logging
from typing import Dict, Any, Optional

from bitget_client import get_client

LOGGER = logging.getLogger(__name__)

# ===============================================================
# Symbol Mapping KuCoin → Bitget
# ===============================================================
def map_symbol(symbol: str) -> str:
    """
    Convertit XBTUSDTM / SOLUSDTM → BTCUSDT_UMCBL / SOLUSDT_UMCBL.
    """
    s = symbol.upper().replace("USDTM", "").replace("USDM", "")
    if s == "XBT":
        s = "BTC"
    return f"{s}USDT_UMCBL"


# ===============================================================
# Desk Lead Execution Layer
# ===============================================================
class BitgetTrader:
    """
    Exécution institutionnelle complète :
    - LIMIT post_only
    - Stop-loss plan
    - TP1 / TP2 reduce-only
    - Partial / BE / trailing
    - Annulation intelligente
    """

    def __init__(self, api_key: str, api_secret: str, api_passphrase: str):
        self.api_key = api_key
        self.api_secret = api_secret
        self.api_passphrase = api_passphrase

        # Anti-duplicate execution
        self.active_entries: Dict[str, bool] = {}

    async def _client(self):
        return await get_client(self.api_key, self.api_secret, self.api_passphrase)

    # ===============================================================
    # Helpers institutionnels
    # ===============================================================
    async def _normalize_qty(self, symbol: str, qty: float) -> float:
        client = await self._client()
        c = await client.get_contract(symbol)
        if not c:
            return qty

        lot = float(c.get("size", 0.001))
        if lot <= 0:
            return qty

        steps = int(qty / lot)
        final_qty = max(steps * lot, lot)
        return float(final_qty)

    # ===============================================================
    # ENTRY LIMIT
    # ===============================================================
    async def place_limit(
        self, symbol: str, side: str, price: float, qty: float,
        post_only: bool = True, margin_coin: str = "USDT"
    ) -> Dict[str, Any]:

        b_symbol = map_symbol(symbol)

        # Anti-duplicate
        key = f"{b_symbol}-{side}"
        if self.active_entries.get(key):
            return {"error": "duplicate_entry_blocked"}

        self.active_entries[key] = True

        client = await self._client()
        qty = await self._normalize_qty(b_symbol, qty)

        body = {
            "symbol": b_symbol,
            "marginCoin": margin_coin,
            "size": str(qty),
            "price": str(price),
            "side": "open_long" if side.upper() == "LONG" else "open_short",
            "orderType": "limit",
        }

        if post_only:
            body["timeInForceValue"] = "post_only"

        r = await client._request("POST", "/api/mix/v1/order/placeOrder", data=body)

        if r.get("code") != "00000":
            self.active_entries[key] = False
            return {"ok": False, "error": r, "symbol": b_symbol}

        return {"ok": True, "order": r, "symbol": b_symbol, "qty": qty}

    # ===============================================================
    # STOP LOSS PLACEMENT
    # ===============================================================
    async def place_stop_loss(
        self, symbol: str, side: str, sl_price: float, qty: float,
        margin_coin: str = "USDT"
    ) -> Dict[str, Any]:

        b_symbol = map_symbol(symbol)
        client = await self._client()
        qty = await self._normalize_qty(b_symbol, qty)

        trigger_side = "close_long" if side.upper() == "LONG" else "close_short"

        body = {
            "symbol": b_symbol,
            "marginCoin": margin_coin,
            "triggerPrice": str(sl_price),
            "executePrice": str(sl_price),
            "orderType": "plan",
            "side": trigger_side,
            "size": str(qty),
            "triggerType": "fill_price",
        }

        r = await client._request("POST", "/api/mix/v1/order/placePlan", data=body)

        if r.get("code") != "00000":
            LOGGER.error(f"[SL ERROR] {r}")
            return {"ok": False, "error": r}

        return {"ok": True, "sl": r}

    # ===============================================================
    # TAKE PROFIT
    # ===============================================================
    async def place_take_profit(
        self, symbol: str, side: str, tp_price: float, qty: float,
        margin_coin: str = "USDT"
    ):

        b_symbol = map_symbol(symbol)
        client = await self._client()
        qty = await self._normalize_qty(b_symbol, qty)

        trigger_side = "close_long" if side.upper() == "LONG" else "close_short"

        body = {
            "symbol": b_symbol,
            "marginCoin": margin_coin,
            "triggerPrice": str(tp_price),
            "executePrice": str(tp_price),
            "orderType": "profit",
            "side": trigger_side,
            "size": str(qty),
            "triggerType": "fill_price",
        }

        r = await client._request("POST", "/api/mix/v1/order/placePlan", data=body)

        if r.get("code") != "00000":
            LOGGER.error(f"[TP ERROR] {r}")
            return {"ok": False, "error": r}

        return {"ok": True, "tp": r}

    # ===============================================================
    # CANCEL ORDERS
    # ===============================================================
    async def cancel_all(self, symbol: str, margin_coin: str = "USDT") -> Dict[str, Any]:
        client = await self._client()
        b_symbol = map_symbol(symbol)

        body = {"symbol": b_symbol, "marginCoin": margin_coin}
        r = await client._request("POST", "/api/mix/v1/order/cancelAllOrders", data=body)

        return r

    # ===============================================================
    # MOVE STOP LOSS
    # ===============================================================
    async def move_stop_loss(
        self, symbol: str, side: str, new_sl: float, qty: float,
        margin_coin: str = "USDT"
    ):
        await self.cancel_all(symbol, margin_coin)
        return await self.place_stop_loss(symbol, side, new_sl, qty, margin_coin)

    # ===============================================================
    # GET POSITION
    # ===============================================================
    async def get_position(self, symbol: str):
        client = await self._client()
        return await client.get_position(map_symbol(symbol))

    # ===============================================================
    # PARTIAL CLOSE MARKET
    # ===============================================================
    async def close_partial(
        self, symbol: str, side: str, qty: float, margin_coin: str = "USDT"
    ):
        client = await self._client()
        b_symbol = map_symbol(symbol)
        qty = await self._normalize_qty(b_symbol, qty)

        exit_side = "close_long" if side.upper() == "LONG" else "close_short"

        body = {
            "symbol": b_symbol,
            "marginCoin": margin_coin,
            "size": str(qty),
            "side": exit_side,
            "orderType": "market",
        }

        r = await client._request("POST", "/api/mix/v1/order/placeOrder", data=body)
        return r
