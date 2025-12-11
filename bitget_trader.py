# =====================================================================
# bitget_trader.py — Desk Lead Execution Engine (Bitget v2 2025)
# =====================================================================
# Règles :
#   - Produit : USDT-FUTURES
#   - Marge cible : ~20 USDT par trade (MARGIN_USDT)
#   - Levier cible : 10x  ⇒ notionnel ≈ 200 USDT (LEVERAGE)
#   - Mode : isolé (marginMode = "isolated")
#   - Entrée : LIMIT
#   - SL / TP : plan orders (place-plan-order)
# =====================================================================

import time
import logging
from typing import Dict, Any, Optional

from settings import MARGIN_USDT, LEVERAGE
from bitget_client import BitgetClient

LOGGER = logging.getLogger(__name__)

PRODUCT_TYPE = "USDT-FUTURES"
MARGIN_COIN = "USDT"
# Bitget v2 : valeurs valides = "isolated" ou "crossed"
MARGIN_MODE = "isolated"


class BitgetTrader(BitgetClient):
    """
    Exécuteur d'ordres Bitget en mode desk lead.

    Hérite de BitgetClient pour réutiliser :
      - auth
      - _request()

    Expose 3 méthodes utilisées par scanner.py :
      - place_limit(symbol, side, price, qty)
      - place_stop_loss(symbol, side, sl, qty)
      - place_take_profit(symbol, side, tp, qty)

    Convention :
      - qty sur l'entrée = multiplicateur de taille (1.0 = taille de base)
      - qty sur SL/TP    = fraction de la position (1.0 = 100%, 0.5 = 50%, etc.)
    """

    def __init__(self, api_key: str, api_secret: str, passphrase: str):
        super().__init__(api_key, api_secret, passphrase)

        # Marge / levier configurables via settings.py
        self.margin_usdt: float = float(MARGIN_USDT or 20.0)
        self.leverage: float = float(LEVERAGE or 10.0)

        # Mémo de la taille d'entrée (en coin) par symbole
        self._entry_size: Dict[str, float] = {}

    # ------------------------------------------------------------------
    # Helpers internes
    # ------------------------------------------------------------------

    def _compute_base_size(self, price: float) -> float:
        """
        Calcule la taille en coin pour viser :
            notionnel ≈ MARGIN_USDT * LEVERAGE.

        Exemple :
          MARGIN_USDT = 20
          LEVERAGE    = 10
          => notionnel ≈ 200 USDT
          => size ≈ 200 / price
        """
        price = float(price)
        if price <= 0:
            return 0.0

        notional_target = self.margin_usdt * self.leverage
        size = notional_target / price
        return float(size)

    @staticmethod
    def _normalize_side_open(side: str) -> str:
        s = (str(side) or "").lower()
        if s in ("buy", "long"):
            return "buy"
        if s in ("sell", "short"):
            return "sell"
        return "buy"

    @staticmethod
    def _close_side_for_open(open_side: str) -> str:
        """
        Renvoie le côté inverse pour fermer la position :
          - LONG (buy)  → close avec 'sell'
          - SHORT (sell) → close avec 'buy'
        """
        o = (str(open_side) or "").lower()
        return "sell" if o == "buy" else "buy"

    # ------------------------------------------------------------------
    # ORDRES
    # ------------------------------------------------------------------

    async def place_limit(
        self,
        symbol: str,
        side: str,
        price: float,
        qty: float,
    ) -> Dict[str, Any]:
        """
        Place un ordre LIMIT pour ouvrir une position.

        - `side`  : "buy" ou "sell"
        - `price` : prix limite
        - `qty`   : multiplicateur de la taille de base (1.0 = taille standard)
        """
        side_open = self._normalize_side_open(side)
        price_f = float(price)

        base_size = self._compute_base_size(price_f)

        try:
            multiple = float(qty)
        except Exception:
            multiple = 1.0

        size = base_size * multiple

        # On mémorise la taille d'entrée pour ce symbole
        self._entry_size[symbol] = size

        approx_notional = size * price_f
        LOGGER.info(
            "[TRADER] place_limit %s %s price=%s size=%.8f  "
            "(notional≈%.2f USDT, marge≈%.2f USDT, levier=%.1fx)",
            symbol,
            side_open,
            price_f,
            size,
            approx_notional,
            self.margin_usdt,
            self.leverage,
        )

        body = {
            "productType": PRODUCT_TYPE,
            "symbol": symbol,
            "marginMode": MARGIN_MODE,
            "marginCoin": MARGIN_COIN,
            "size": f"{size:.10f}",
            "price": f"{price_f:.10f}",
            "orderType": "limit",
            "side": side_open,
            "tradeSide": "open",     # hedge-mode compatible
            "force": "gtc",
            "reduceOnly": "NO",
            "clientOid": f"entry-{symbol}-{int(time.time() * 1000)}",
        }

        res = await self._request(
            "POST",
            "/api/v2/mix/order/place-order",
            data=body,
            auth=True,
        )

        ok = isinstance(res, dict) and res.get("code") == "00000"
        if not ok:
            LOGGER.error(
                "[TRADER] ❌ place_limit FAILED %s %s price=%s size=%.8f → %s",
                symbol,
                side_open,
                price_f,
                size,
                res,
            )

        return {"ok": ok, "raw": res, "size": size}

    # ------------------------------------------------------------------

    async def place_stop_loss(
        self,
        symbol: str,
        side: str,
        sl: float,
        qty: float,
    ) -> Dict[str, Any]:
        """
        Place un stop loss en plan order.

        - `side` : côté de l'ORDRE D'OUVERTURE ("buy"/"sell")
        - `sl`   : prix de déclenchement + exécution (trigger + limit)
        - `qty`  : fraction de la position à couvrir (1.0 = 100%)

        Utilise :
          - planType    = "normal_plan"
          - triggerType = "mark_price"
          - tradeSide   = "close"
          - reduceOnly  = "YES"
        """
        trigger_price = float(sl)

        # Taille d'entrée mémorisée
        entry_size = self._entry_size.get(symbol)
        if entry_size is None:
            # Fallback défensif (ne devrait pas arriver si place_limit a été appelé avant)
            entry_size = self._compute_base_size(trigger_price)

        try:
            fraction = float(qty)
        except Exception:
            fraction = 1.0

        size = max(entry_size * fraction, 0.0)

        trigger_side = self._close_side_for_open(side)

        LOGGER.info(
            "[TRADER] place_stop_loss %s side(open)=%s trigger_side=%s "
            "sl=%s size=%.8f (fraction=%.3f)",
            symbol,
            side,
            trigger_side,
            trigger_price,
            size,
            fraction,
        )

        body = {
            "planType": "normal_plan",
            "productType": PRODUCT_TYPE,
            "symbol": symbol,
            "marginMode": MARGIN_MODE,
            "marginCoin": MARGIN_COIN,
            "size": f"{size:.10f}",
            "price": f"{trigger_price:.10f}",
            "triggerPrice": f"{trigger_price:.10f}",
            "triggerType": "mark_price",
            "orderType": "limit",
            "side": trigger_side,
            "tradeSide": "close",
            "reduceOnly": "YES",
            "clientOid": f"sl-{symbol}-{int(time.time() * 1000)}",
        }

        res = await self._request(
            "POST",
            "/api/v2/mix/order/place-plan-order",
            data=body,
            auth=True,
        )
        ok = isinstance(res, dict) and res.get("code") == "00000"

        if not ok:
            LOGGER.error("[TRADER] ❌ place_stop_loss FAILED %s → %s", symbol, res)

        return {"ok": ok, "raw": res, "size": size}

    # ------------------------------------------------------------------

    async def place_take_profit(
        self,
        symbol: str,
        side: str,
        tp: float,
        qty: float,
    ) -> Dict[str, Any]:
        """
        Place un take profit en plan order.

        - `side` : côté de l'ORDRE D'OUVERTURE ("buy"/"sell")
        - `tp`   : prix de TP (trigger + limit)
        - `qty`  : fraction de la position à prendre (0.5 = 50%, etc.)

        Utilise la même logique que place_stop_loss :
          - planType    = "normal_plan"
          - triggerType = "mark_price"
          - tradeSide   = "close"
          - reduceOnly  = "YES"
        """
        trigger_price = float(tp)

        entry_size = self._entry_size.get(symbol)
        if entry_size is None:
            entry_size = self._compute_base_size(trigger_price)

        try:
            fraction = float(qty)
        except Exception:
            fraction = 1.0

        size = max(entry_size * fraction, 0.0)

        trigger_side = self._close_side_for_open(side)

        LOGGER.info(
            "[TRADER] place_take_profit %s side(open)=%s trigger_side=%s "
            "tp=%s size=%.8f (fraction=%.3f)",
            symbol,
            side,
            trigger_side,
            trigger_price,
            size,
            fraction,
        )

        body = {
            "planType": "normal_plan",
            "productType": PRODUCT_TYPE,
            "symbol": symbol,
            "marginMode": MARGIN_MODE,
            "marginCoin": MARGIN_COIN,
            "size": f"{size:.10f}",
            "price": f"{trigger_price:.10f}",
            "triggerPrice": f"{trigger_price:.10f}",
            "triggerType": "mark_price",
            "orderType": "limit",
            "side": trigger_side,
            "tradeSide": "close",
            "reduceOnly": "YES",
            "clientOid": f"tp-{symbol}-{int(time.time() * 1000)}",
        }

        res = await self._request(
            "POST",
            "/api/v2/mix/order/place-plan-order",
            data=body,
            auth=True,
        )
        ok = isinstance(res, dict) and res.get("code") == "00000"

        if not ok:
            LOGGER.error("[TRADER] ❌ place_take_profit FAILED %s → %s", symbol, res)

        return {"ok": ok, "raw": res, "size": size}
