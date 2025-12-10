# =====================================================================
# bitget_trader.py — Desk Lead Execution Engine (Bitget v2 2025)
# =====================================================================
# 100% NATIF BITGET :
#   ✔ LIMIT
#   ✔ STOP LOSS (plan order)
#   ✔ TAKE PROFIT (plan order)
#   ✔ Qty en contrat (size)
# =====================================================================

import hmac
import base64
import hashlib
from typing import Dict, Any, Optional

from bitget_client import get_client


class BitgetTrader:

    def __init__(self, api_key: str, api_secret: str, api_passphrase: str):
        self.api_key = api_key
        self.api_secret = api_secret.encode()
        self.api_passphrase = api_passphrase

    # ------------------------------------------------------------
    # SIGNATURE (pas utilisée directement ici, tout passe par client)
    # ------------------------------------------------------------
    def _sign(self, ts: str, method: str, path: str, query: str, body: str) -> str:
        msg = f"{ts}{method}{path}{query}{body}"
        mac = hmac.new(self.api_secret, msg.encode(), hashlib.sha256).digest()
        return base64.b64encode(mac).decode()

    # ------------------------------------------------------------
    async def _request(
        self,
        method: str,
        path: str,
        *,
        params: Optional[Dict[str, Any]] = None,
        data: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """
        Délégation à bitget_client (gestion session + retry + logs).
        """
        client = await get_client(self.api_key, self.api_secret.decode(), self.api_passphrase)
        return await client._request(method, path, params=params, data=data, auth=True)

    # ============================================================
    # HELPERS SIDE
    # ============================================================

    @staticmethod
    def _is_long(side: str) -> bool:
        s = side.lower()
        return s in ("long", "buy")

    # ------------------------------------------------------------
    # LIMIT ORDER
    # ------------------------------------------------------------
    async def place_limit(self, symbol: str, side: str, price: float, qty: float) -> Dict[str, Any]:
        """
        Bitget v2 limit order — symbol ex: BTCUSDT
        side: "BUY"/"SELL" ou "LONG"/"SHORT"
        """
        side_final = "buy" if self._is_long(side) else "sell"

        data = {
            "symbol": symbol,
            "marginCoin": "USDT",
            "size": str(qty),
            "price": str(price),
            "orderType": "limit",
            "side": side_final,
        }

        return await self._request(
            "POST",
            "/api/v2/mix/order/place-order",
            data=data,
        )

    # ------------------------------------------------------------
    # STOP LOSS
    # ------------------------------------------------------------
    async def place_stop_loss(self, symbol: str, side: str, sl: float, qty: float) -> Dict[str, Any]:
        """
        Plan order SL :
        - Si position LONG/BUY → stop = SELL
        - Si position SHORT/SELL → stop = BUY
        """
        close_side = "sell" if self._is_long(side) else "buy"

        data = {
            "symbol": symbol,
            "marginCoin": "USDT",
            "size": str(qty),
            "triggerPrice": str(sl),
            "triggerType": "mark_price",
            "executePrice": str(sl),
            "orderType": "limit",
            "side": close_side,
        }

        return await self._request(
            "POST",
            "/api/v2/mix/order/place-plan-order",
            data=data,
        )

    # ------------------------------------------------------------
    # TAKE PROFIT
    # ------------------------------------------------------------
    async def place_take_profit(self, symbol: str, side: str, tp: float, qty: float) -> Dict[str, Any]:
        close_side = "sell" if self._is_long(side) else "buy"

        data = {
            "symbol": symbol,
            "marginCoin": "USDT",
            "size": str(qty),
            "triggerPrice": str(tp),
            "triggerType": "mark_price",
            "executePrice": str(tp),
            "orderType": "limit",
            "side": close_side,
        }

        return await self._request(
            "POST",
            "/api/v2/mix/order/place-plan-order",
            data=data,
        )
