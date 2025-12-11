# =====================================================================
# bitget_trader.py — Desk Lead Execution Engine (Bitget v2 2025)
# =====================================================================
# 100% NATIF BITGET FUTURES V2 :
#   ✔ LIMIT (place-order)
#   ✔ STOP LOSS (place-plan-order)
#   ✔ TAKE PROFIT (place-plan-order)
#   ✔ Qty en contrat (size)
#   ✔ Cross margin, USDT-FUTURES
# =====================================================================

import time
import json
import logging
from typing import Dict, Any

from bitget_client import get_client

LOGGER = logging.getLogger(__name__)


class BitgetTrader:
    """
    Exécution dérivés Bitget (USDT-FUTURES, cross margin, v2)
    """

    PRODUCT_TYPE = "USDT-FUTURES"
    MARGIN_MODE = "crossed"
    MARGIN_COIN = "USDT"

    def __init__(self, api_key: str, api_secret: str, api_passphrase: str):
        self.api_key = api_key
        self.api_secret = api_secret
        self.api_passphrase = api_passphrase

    # ------------------------------------------------------------------
    async def _request(self, method: str, path: str, *, params=None, data=None) -> Dict[str, Any]:
        """
        Délégation à BitgetClient (signature + retry + logs).
        On ne refait PAS la signature ici.
        """
        client = await get_client(self.api_key, self.api_secret, self.api_passphrase)
        return await client._request(method, path, params=params, data=data, auth=True)

    # ==================================================================
    # LIMIT ORDER (ENTRY)
    # ==================================================================
    async def place_limit(self, symbol: str, side: str, price: float, qty: float) -> Dict[str, Any]:
        """
        Place un ordre LIMIT sur USDT-FUTURES.

        - symbol: "BTCUSDT", "PENDLEUSDT", etc.
        - side: "long" / "buy" / "short" / "sell"
        - price: prix limite
        - qty: taille en contrats (size)
        """

        s = side.lower()
        if s in ("long", "buy"):
            side_final = "buy"
        elif s in ("short", "sell"):
            side_final = "sell"
        else:
            raise ValueError(f"Invalid side: {side}")

        client_oid = f"entry-{symbol}-{int(time.time() * 1000)}"

        data = {
            "productType": self.PRODUCT_TYPE,      # USDT-FUTURES
            "symbol": symbol,
            "marginMode": self.MARGIN_MODE,        # crossed
            "marginCoin": self.MARGIN_COIN,       # USDT
            "size": str(qty),
            "price": str(price),
            "orderType": "limit",
            "side": side_final,                   # buy / sell
            "tradeSide": "open",                  # ouverture de position
            # ⚠️ IMPORTANT : time-in-force pour v2
            # gtc = good-till-cancelled
            "force": "gtc",
            "reduceOnly": "NO",
            "clientOid": client_oid,
        }

        LOGGER.info(f"[TRADER] place_limit {symbol} {side_final} {price} size={qty}")
        res = await self._request("POST", "/api/v2/mix/order/place-order", data=data)

        ok = res.get("code") == "00000"
        if not ok:
            LOGGER.error(f"[TRADER] LIMIT ERROR {symbol} → {json.dumps(res)}")

        return {"ok": ok, "raw": res, "clientOid": client_oid}

    # ==================================================================
    # STOP LOSS (PLAN ORDER)
    # ==================================================================
    async def place_stop_loss(self, symbol: str, side: str, sl: float, qty: float) -> Dict[str, Any]:
        """
        Place un STOP LOSS via plan order v2.

        - Pour un LONG → SL = SELL / close / reduceOnly
        - Pour un SHORT → SL = BUY / close / reduceOnly
        """

        s = side.lower()
        if s in ("long", "buy"):
            order_side = "sell"
        elif s in ("short", "sell"):
            order_side = "buy"
        else:
            raise ValueError(f"Invalid side: {side}")

        client_oid = f"sl-{symbol}-{int(time.time() * 1000)}"

        data = {
            "planType": "normal_plan",            # simple trigger
            "productType": self.PRODUCT_TYPE,
            "symbol": symbol,
            "marginMode": self.MARGIN_MODE,
            "marginCoin": self.MARGIN_COIN,
            "size": str(qty),
            "price": str(sl),                     # prix d'exécution
            "triggerPrice": str(sl),              # prix de déclenchement
            "triggerType": "mark_price",          # mark price pour SL propre
            "side": order_side,                   # direction de l'ordre
            "tradeSide": "close",                 # fermeture de position
            "orderType": "limit",
            "reduceOnly": "YES",
            "clientOid": client_oid,
        }

        LOGGER.info(f"[TRADER] place_stop_loss {symbol} side={order_side} sl={sl} size={qty}")
        res = await self._request("POST", "/api/v2/mix/order/place-plan-order", data=data)

        ok = res.get("code") == "00000"
        if not ok:
            LOGGER.error(f"[TRADER] SL ERROR {symbol} → {json.dumps(res)}")

        return {"ok": ok, "raw": res, "clientOid": client_oid}

    # ==================================================================
    # TAKE PROFIT (PLAN ORDER)
    # ==================================================================
    async def place_take_profit(self, symbol: str, side: str, tp: float, qty: float) -> Dict[str, Any]:
        """
        Place un TAKE PROFIT via plan order v2.

        - Pour un LONG → TP = SELL / close / reduceOnly
        - Pour un SHORT → TP = BUY / close / reduceOnly
        """

        s = side.lower()
        if s in ("long", "buy"):
            order_side = "sell"
        elif s in ("short", "sell"):
            order_side = "buy"
        else:
            raise ValueError(f"Invalid side: {side}")

        client_oid = f"tp-{symbol}-{int(time.time() * 1000)}"

        data = {
            "planType": "normal_plan",
            "productType": self.PRODUCT_TYPE,
            "symbol": symbol,
            "marginMode": self.MARGIN_MODE,
            "marginCoin": self.MARGIN_COIN,
            "size": str(qty),
            "price": str(tp),
            "triggerPrice": str(tp),
            "triggerType": "mark_price",
            "side": order_side,
            "tradeSide": "close",
            "orderType": "limit",
            "reduceOnly": "YES",
            "clientOid": client_oid,
        }

        LOGGER.info(f"[TRADER] place_take_profit {symbol} side={order_side} tp={tp} size={qty}")
        res = await self._request("POST", "/api/v2/mix/order/place-plan-order", data=data)

        ok = res.get("code") == "00000"
        if not ok:
            LOGGER.error(f"[TRADER] TP ERROR {symbol} → {json.dumps(res)}")

        return {"ok": ok, "raw": res, "clientOid": client_oid}
