# =====================================================================
# bitget_trader.py — Desk Lead Execution Engine (Bitget v2 2025)
# =====================================================================
# 100% NATIF BITGET FUTURES :
#   ✔ LIMIT (entrée)
#   ✔ STOP LOSS (plan order, reduce-only)
#   ✔ TAKE PROFIT (plan order, reduce-only)
#   ✔ Qty en contrats (size)
# =====================================================================

import time
from typing import Dict, Any

from bitget_client import get_client


class BitgetTrader:
    """
    Exécution centralisée Bitget v2 Futures.
    On délègue la partie HTTP / signature au BitgetClient.
    """

    def __init__(self, api_key: str, api_secret: str, api_passphrase: str):
        self.api_key = api_key
        self.api_secret = api_secret          # string (BitgetClient va encoder)
        self.api_passphrase = api_passphrase

        # Paramètres Futures USDT par défaut
        self.product_type = "USDT-FUTURES"    # conforme à ce que tu utilises pour /contracts
        self.margin_mode = "crossed"          # ou "isolated" si tu préfères
        self.margin_coin = "USDT"

    # ------------------------------------------------------------
    async def _request(self, method: str, path: str, *, params=None, data=None) -> Dict[str, Any]:
        """
        Délègue à bitget_client (gestion session + signature + retry).
        Renvoie la réponse JSON brute de Bitget.
        """
        client = await get_client(self.api_key, self.api_secret, self.api_passphrase)
        return await client._request(method, path, params=params, data=data, auth=True)

    # ------------------------------------------------------------
    def _client_oid(self, symbol: str, tag: str) -> str:
        """
        Génère un clientOid unique côté desk (utile pour debug & idempotence).
        """
        ts = int(time.time() * 1000)
        return f"{tag}-{symbol}-{ts}"

    # ------------------------------------------------------------
    def _wrap_result(self, res: Any) -> Dict[str, Any]:
        """
        Uniformise le résultat pour le scanner:
          - ok = True si code == '00000'
          - raw = réponse brute Bitget
        """
        if isinstance(res, dict) and res.get("code") == "00000":
            return {"ok": True, "raw": res}
        return {"ok": False, "raw": res}

    # ------------------------------------------------------------
    # LIMIT ORDER (ENTRÉE)
    # ------------------------------------------------------------
    async def place_limit(self, symbol: str, side: str, price: float, qty: float) -> Dict[str, Any]:
        """
        Place un ordre LIMIT sur Bitget v2 Futures.
        symbol ex: BTCUSDT, CROSSUSDT, etc.
        side : "long"/"buy" ou "short"/"sell"
        """
        side_norm = side.lower()
        side_final = "buy" if side_norm in ("long", "buy") else "sell"

        data = {
            "productType": self.product_type,      # ❗ obligatoire
            "symbol": symbol,
            "marginMode": self.margin_mode,        # ❗ obligatoire
            "marginCoin": self.margin_coin,        # USDT
            "size": str(qty),
            "price": str(price),
            "orderType": "limit",
            "side": side_final,                    # buy/sell
            "tradeSide": "open",                   # on ouvre une position
            "force": "normal",                     # normal, post_only, fok, ioc (ici normal)
            "reduceOnly": "NO",
            "clientOid": self._client_oid(symbol, "entry"),
        }

        res = await self._request(
            "POST",
            "/api/v2/mix/order/place-order",
            data=data,
        )
        return self._wrap_result(res)

    # ------------------------------------------------------------
    # STOP LOSS (PLAN ORDER, REDUCE-ONLY)
    # ------------------------------------------------------------
    async def place_stop_loss(self, symbol: str, side: str, sl: float, qty: float) -> Dict[str, Any]:
        """
        Crée un plan order STOP LOSS sur Bitget v2 Futures:
          - planType: normal_plan
          - tradeSide: close
          - reduceOnly: YES
        """
        side_norm = side.lower()
        # Pour un long -> SL = sell, pour un short -> SL = buy
        trigger_side = "sell" if side_norm in ("long", "buy") else "buy"

        data = {
            "productType": self.product_type,
            "symbol": symbol,
            "marginMode": self.margin_mode,
            "marginCoin": self.margin_coin,

            "planType": "normal_plan",
            "size": str(qty),
            "price": str(sl),                # prix d'exécution limite
            "triggerPrice": str(sl),         # prix de déclenchement
            "triggerType": "mark_price",     # mark_price / last_price

            "side": trigger_side,            # sens de l'ordre sur le book
            "tradeSide": "close",            # on ferme la position
            "orderType": "limit",
            "reduceOnly": "YES",

            "clientOid": self._client_oid(symbol, "sl"),
        }

        res = await self._request(
            "POST",
            "/api/v2/mix/order/place-plan-order",
            data=data,
        )
        return self._wrap_result(res)

    # ------------------------------------------------------------
    # TAKE PROFIT (PLAN ORDER, REDUCE-ONLY)
    # ------------------------------------------------------------
    async def place_take_profit(self, symbol: str, side: str, tp: float, qty: float) -> Dict[str, Any]:
        """
        Crée un plan order TAKE PROFIT sur Bitget v2 Futures:
          - planType: normal_plan
          - tradeSide: close
          - reduceOnly: YES
        """
        side_norm = side.lower()
        trigger_side = "sell" if side_norm in ("long", "buy") else "buy"

        data = {
            "productType": self.product_type,
            "symbol": symbol,
            "marginMode": self.margin_mode,
            "marginCoin": self.margin_coin,

            "planType": "normal_plan",
            "size": str(qty),
            "price": str(tp),
            "triggerPrice": str(tp),
            "triggerType": "mark_price",

            "side": trigger_side,
            "tradeSide": "close",
            "orderType": "limit",
            "reduceOnly": "YES",

            "clientOid": self._client_oid(symbol, "tp"),
        }

        res = await self._request(
            "POST",
            "/api/v2/mix/order/place-plan-order",
            data=data,
        )
        return self._wrap_result(res)
