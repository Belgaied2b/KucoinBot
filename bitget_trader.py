# =====================================================================
# bitget_trader.py — Desk Lead Execution Engine (Bitget v2 2025)
# =====================================================================
# Règles :
#   - Produit : USDT-FUTURES
#   - Marge cible : ~20 USDT par trade
#   - Levier cible : 10x  ⇒ notionnel ≈ 200 USDT
#   - Mode : isolé (marginMode = fixed)
#   - Entrée : LIMIT
#   - SL / TP : plan orders (place-plan-order)
# =====================================================================

import time
import logging
from typing import Dict, Any

from bitget_client import get_client

LOGGER = logging.getLogger(__name__)

# ---------------------------------------------------------------------
# CONFIG GLOBALE
# ---------------------------------------------------------------------
PRODUCT_TYPE = "USDT-FUTURES"
MARGIN_COIN = "USDT"

# Sur Bitget, "fixed" = isolated, "crossed" = cross
MARGIN_MODE = "fixed"          # <--- ISOLÉ

TARGET_MARGIN_USDT = 20.0      # marge désirée
TARGET_LEVERAGE = 10.0         # levier désiré
TIME_IN_FORCE = "gtc"          # validité de l'ordre


class BitgetTrader:
    """
    Trader institutionnel Bitget :
      - calcule la taille à partir du prix pour viser ~20 USDT de marge (levier 10x)
      - conserve la même interface que le bot KuCoin côté scanner
    """

    def __init__(self, api_key: str, api_secret: str, api_passphrase: str):
        self.api_key = api_key
        self.api_secret = api_secret
        self.api_passphrase = api_passphrase

        self._client = None

        # On mémorise la taille "de base" envoyée à l'entrée pour ce symbole
        # afin de pouvoir poser SL / TP cohérents (et fractions pour les TP).
        self._entry_size: Dict[str, float] = {}
        self._entry_price: Dict[str, float] = {}

    # ------------------------------------------------------------------
    async def _ensure_client(self):
        if self._client is None:
            self._client = await get_client(
                self.api_key,
                self.api_secret,
                self.api_passphrase,
            )
        return self._client

    async def _request(self, method: str, path: str, *, params=None, data=None) -> Dict[str, Any]:
        client = await self._ensure_client()
        return await client._request(method, path, params=params, data=data, auth=True)

    # ------------------------------------------------------------------
    # CALCUL DE LA TAILLE : ~20 USDT de marge (notionnel ≈ 200 USDT)
    # ------------------------------------------------------------------
    def _compute_base_size(self, price: float) -> float:
        """
        Calcule la taille en COIN (pas en USDT) pour viser :
          notionnel ≈ 200 USDT = TARGET_MARGIN_USDT * TARGET_LEVERAGE
        size = notionnel / prix
        """
        try:
            p = float(price)
        except Exception:
            return 0.0

        if p <= 0:
            return 0.0

        notional_target = TARGET_MARGIN_USDT * TARGET_LEVERAGE  # 20 * 10 = 200
        size = notional_target / p
        return max(size, 0.0)

    # ------------------------------------------------------------------
    # ORDRE LIMIT D'ENTRÉE
    # ------------------------------------------------------------------
    async def place_limit(self, symbol: str, side: str, price: float, qty: float) -> Dict[str, Any]:
        """
        place_limit(symbol, side, price, qty)

        - `qty` est gardé pour compat mais utilisé comme *multiplicateur* de taille.
        - La taille de base est calculée à partir du prix pour viser ~20 USDT de marge.
        """
        base_size = self._compute_base_size(price)

        # qty joue le rôle de multiplicateur (par défaut 1.0 dans ton bot)
        try:
            multiple = float(qty)
        except Exception:
            multiple = 1.0

        size = base_size * multiple

        # On mémorise la taille de base (avant fractions TP) pour ce symbole
        self._entry_size[symbol] = base_size
        self._entry_price[symbol] = float(price)

        side_final = "buy" if side.lower() in ("buy", "long") else "sell"

        body = {
            "productType": PRODUCT_TYPE,
            "symbol": symbol,
            "marginMode": MARGIN_MODE,
            "marginCoin": MARGIN_COIN,
            "size": str(size),
            "price": str(price),
            "orderType": "limit",
            "side": side_final,       # buy / sell
            "tradeSide": "open",      # open position
            "force": TIME_IN_FORCE,   # gtc / ioc / fok / post_only
            "reduceOnly": "NO",
            "clientOid": f"entry-{symbol}-{int(time.time() * 1000)}",
        }

        notional = size * float(price)
        approx_margin = notional / TARGET_LEVERAGE if TARGET_LEVERAGE > 0 else 0.0

        LOGGER.info(
            "[TRADER] place_limit %s %s price=%s size=%.8f  "
            "(notional≈%.2f USDT, marge≈%.2f USDT, levier=%sx)",
            symbol,
            side_final,
            price,
            size,
            notional,
            approx_margin,
            TARGET_LEVERAGE,
        )

        res = await self._request("POST", "/api/v2/mix/order/place-order", data=body)
        ok = isinstance(res, dict) and res.get("code") == "00000"

        if not ok:
            LOGGER.error("[TRADER] ❌ place_limit FAILED %s → %s", symbol, res)

        return {
            "ok": ok,
            "raw": res,
            "symbol": symbol,
            "side": side_final,
            "price": price,
            "size": size,
            "base_size": base_size,
        }

    # ------------------------------------------------------------------
    # STOP LOSS (PLAN ORDER)
    # ------------------------------------------------------------------
    async def place_stop_loss(self, symbol: str, side: str, sl: float, qty: float) -> Dict[str, Any]:
        """
        place_stop_loss(symbol, side, sl, qty)

        - On utilise la taille de base mémorisée à l'entrée.
        - `qty` est traité comme fraction de cette taille (1.0 = 100%, 0.5 = 50%, etc.).
        Dans ton scanner actuel, tu envoies 1.0 pour le SL => taille complète.
        """
        base_size = self._entry_size.get(symbol)
        if base_size is None:
            # fallback improbable, mais on recalcule une taille de base
            base_size = self._compute_base_size(sl)

        try:
            fraction = float(qty)
        except Exception:
            fraction = 1.0

        size = max(base_size * fraction, 0.0)

        # Pour un long on vend au SL, pour un short on achète au SL
        trigger_side = "sell" if side.lower() in ("buy", "long") else "buy"

        body = {
            "productType": PRODUCT_TYPE,
            "symbol": symbol,
            "marginMode": MARGIN_MODE,
            "marginCoin": MARGIN_COIN,
            "size": str(size),
            "orderType": "limit",
            "side": trigger_side,
            "tradeSide": "close",
            "triggerType": "mark_price",
            "triggerPrice": str(sl),
            "executePrice": str(sl),
            "reduceOnly": "YES",
            "clientOid": f"sl-{symbol}-{int(time.time() * 1000)}",
        }

        LOGGER.info(
            "[TRADER] place_stop_loss %s side=%s sl=%s size=%.8f",
            symbol,
            trigger_side,
            sl,
            size,
        )

        res = await self._request("POST", "/api/v2/mix/order/place-plan-order", data=body)
        ok = isinstance(res, dict) and res.get("code") == "00000"

        if not ok:
            LOGGER.error("[TRADER] ❌ place_stop_loss FAILED %s → %s", symbol, res)

        return {"ok": ok, "raw": res, "size": size}

    # ------------------------------------------------------------------
    # TAKE PROFIT (PLAN ORDER)
    # ------------------------------------------------------------------
    async def place_take_profit(self, symbol: str, side: str, tp: float, qty: float) -> Dict[str, Any]:
        """
        place_take_profit(symbol, side, tp, qty)

        - Même logique que le SL : `qty` est une fraction de la taille d'entrée.
        - Typiquement :
            TP1 → qty = 0.5
            TP2 → qty = 0.5
          Avec base_size ~200/price, ça donne 50% / 50% de la position.
        """
        base_size = self._entry_size.get(symbol)
        if base_size is None:
            base_size = self._compute_base_size(tp)

        try:
            fraction = float(qty)
        except Exception:
            fraction = 1.0

        size = max(base_size * fraction, 0.0)

        # Pour un long on vend au TP, pour un short on achète au TP
        trigger_side = "sell" if side.lower() in ("buy", "long") else "buy"

        body = {
            "productType": PRODUCT_TYPE,
            "symbol": symbol,
            "marginMode": MARGIN_MODE,
            "marginCoin": MARGIN_COIN,
            "size": str(size),
            "orderType": "limit",
            "side": trigger_side,
            "tradeSide": "close",
            "triggerType": "mark_price",
            "triggerPrice": str(tp),
            "executePrice": str(tp),
            "reduceOnly": "YES",
            "clientOid": f"tp-{symbol}-{int(time.time() * 1000)}",
        }

        LOGGER.info(
            "[TRADER] place_take_profit %s side=%s tp=%s size=%.8f (fraction=%.3f)",
            symbol,
            trigger_side,
            tp,
            size,
            fraction,
        )

        res = await self._request("POST", "/api/v2/mix/order/place-plan-order", data=body)
        ok = isinstance(res, dict) and res.get("code") == "00000"

        if not ok:
            LOGGER.error("[TRADER] ❌ place_take_profit FAILED %s → %s", symbol, res)

        return {"ok": ok, "raw": res, "size": size}
