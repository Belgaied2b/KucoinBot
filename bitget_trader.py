# =====================================================================
# bitget_trader.py — Desk Lead Execution Engine (2025, API v2)
# =====================================================================
# Exécute :
#   ✔ LIMIT ORDER (entry)
#   ✔ STOP LOSS (plan-order)
#   ✔ TAKE PROFIT (plan-order)
#   ✔ Qty en "size" (USDT-M futures)
#
# Totalement compatible :
#   - scanner.py
#   - analyze_signal.py
#   - bitget_client.py v2 (2025)
# =====================================================================

import hmac
import base64
import hashlib
import json
from typing import Dict, Any, Optional

from bitget_client import (
    get_client,
    normalize_symbol,
    add_suffix
)


class BitgetTrader:

    def __init__(self, api_key: str, api_secret: str, api_passphrase: str):
        self.api_key = api_key
        self.api_secret = api_secret
        self.api_passphrase = api_passphrase

    # ------------------------------------------------------------------
    # INTERNAL REQUEST HANDLER (uses bitget_client session + signature)
    # ------------------------------------------------------------------
    async def _signed(self, method: str, path: str, *, params=None, data=None):
        """
        On utilise directement _request() du client Bitget central.
        Cela garantit :
            ✔ signature correcte
            ✔ rate limit partagé
            ✔ session unique
        """
        client = await get_client(self.api_key, self.api_secret, self.api_passphrase)
        return await client._request(method, path, params=params, data=data, auth=True)

    # ------------------------------------------------------------------
    # NORMALISATION SYMBOLS
    # ------------------------------------------------------------------
    def _resolve_symbol(self, symbol: str) -> str:
        """
        Exemples :
        Entrée : BTCUSDT_UMCBL, BTCUSDTM, BTCUSDT
        Sortie : BTCUSDT_UMCBL
        """
        base = normalize_symbol(symbol)       # ex : BTCUSDT
        final = add_suffix(base)             # ex : BTCUSDT_UMCBL
        return final

    # ------------------------------------------------------------------
    # LIMIT ORDER (ENTRY)
    # ------------------------------------------------------------------
    async def place_limit(self, symbol: str, side: str, price: float, qty: float):
        mapped = self._resolve_symbol(symbol)

        side_final = "buy" if side.lower() in ("long", "buy") else "sell"

        data = {
            "symbol": mapped,
            "marginCoin": "USDT",
            "orderType": "limit",
            "side": side_final,
            "price": str(price),
            "size": str(qty),
        }

        return await self._signed(
            "POST",
            "/api/v2/mix/order/place-order",
            data=data
        )

    # ------------------------------------------------------------------
    # STOP LOSS (PLAN ORDER)
    # ------------------------------------------------------------------
    async def place_stop_loss(self, symbol: str, side: str, sl: float, qty: float):
        mapped = self._resolve_symbol(symbol)

        # Pour SL :
        # LONG → stop = SELL
        # SHORT → stop = BUY
        side_final = "sell" if side.lower() in ("long", "buy") else "buy"

        data = {
            "symbol": mapped,
            "marginCoin": "USDT",
            "size": str(qty),

            # Trigger SL
            "triggerType": "mark_price",
            "triggerPrice": str(sl),

            # Exécution au même prix
            "executePrice": str(sl),
            "orderType": "limit",

            "side": side_final
        }

        return await self._signed(
            "POST",
            "/api/v2/mix/order/place-plan-order",
            data=data
        )

    # ------------------------------------------------------------------
    # TAKE PROFIT (PLAN ORDER)
    # ------------------------------------------------------------------
    async def place_take_profit(self, symbol: str, side: str, tp: float, qty: float):
        mapped = self._resolve_symbol(symbol)

        # Pour TP :
        # LONG → vend TP → SELL
        # SHORT → achète TP → BUY
        side_final = "sell" if side.lower() in ("long", "buy") else "buy"

        data = {
            "symbol": mapped,
            "marginCoin": "USDT",
            "size": str(qty),

            "triggerType": "mark_price",
            "triggerPrice": str(tp),

            "executePrice": str(tp),
            "orderType": "limit",

            "side": side_final
        }

        return await self._signed(
            "POST",
            "/api/v2/mix/order/place-plan-order",
            data=data
        )
