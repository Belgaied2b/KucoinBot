# -*- coding: utf-8 -*-
"""
kucoin_adapter.py — Pont simplifié vers KuCoin Futures
- S'aligne sur KucoinTrader (valueQty + leverage)
- Expose: place_limit_order, get_symbol_meta, get_order_by_client_oid
- Loggue et remonte les champs utiles (ok, code, msg, orderId/clientOid)
"""

import time
from typing import Dict, Any, Optional

from logger_utils import get_logger
from kucoin_utils import fetch_symbol_meta
from kucoin_trader import KucoinTrader

log = get_logger("kucoin.adapter")

# instance unique réutilisée (keep-alive httpx)
_TRADER: Optional[KucoinTrader] = None


def _trader() -> KucoinTrader:
    global _TRADER
    if _TRADER is None:
        _TRADER = KucoinTrader()
    return _TRADER


def get_symbol_meta(symbol: str) -> Dict[str, Any]:
    """
    Renvoie la meta d'un symbole au format de fetch_symbol_meta().
    Les clés attendues: priceIncrement, lotSize, etc.
    """
    try:
        meta = fetch_symbol_meta()
        # meta keys comme "BTCUSDT": {"symbol_api": "BTCUSDTM", ...}
        # on tente par symbol (ex: BTCUSDTM) et par racine (BTCUSDT)
        s = symbol.upper().strip()
        # essayer direct
        for k, v in meta.items():
            if str(v.get("symbol_api", "")).upper() == s:
                return v or {}
        # fallback: enlever le "M" final si fourni
        if s.endswith("M"):
            core = s[:-1]
            if core in meta:
                return meta.get(core, {}) or {}
        # dernier fallback: renvoyer dict vide
    except Exception as e:
        log.warning("get_symbol_meta KO: %s", e)
    return {}


def get_order_by_client_oid(client_oid: str) -> Optional[Dict[str, Any]]:
    """
    Wrapper simple vers KucoinTrader.get_order_by_client_oid
    Renvoie None si pas trouvé / erreur, sinon un dict KuCoin (avec .data dedans).
    """
    try:
        t = _trader()
        data = t.get_order_by_client_oid(client_oid)
        # t.get_order_by_client_oid renvoie déjà js.get("data") si ok → data = {...} ou None
        if isinstance(data, dict):
            # normalise quelques champs racine
            out = dict(data)
            if "orderId" not in out and data.get("id"):
                out["orderId"] = data["id"]
            if "clientOid" not in out and data.get("clientOid"):
                out["clientOid"] = data["clientOid"]
            return out
        return None
    except Exception as e:
        log.warning("get_order_by_client_oid(%s) KO: %s", client_oid, e)
        return None


def place_limit_order(
    symbol: str,
    side: str,
    price: float,
    value_usdt: float,
    sl: float = 0.0,
    tp1: float = 0.0,
    tp2: float = 0.0,
    post_only: bool = True,
) -> Dict[str, Any]:
    """
    Crée un LIMIT d'entrée via KucoinTrader.place_limit (valueQty + leverage).
    Retourne un dict normalisé:
      {
        "ok": bool,
        "code": "...",            # code d'erreur éventuel
        "msg": "...",             # message éventuel
        "orderId": "...",         # si disponible
        "clientOid": "...",       # si disponible
        "raw": {...}              # réponse complète KuCoin (si dispo)
      }
    NB: La pose des SL/TP réels (stop orders) n'est pas faite ici pour rester simple.
    """
    t = _trader()

    # KuCoin veut buy/sell en minuscule
    sd = side.lower().strip()
    if sd not in ("buy", "sell"):
        # on mappe "long"/"short" → "buy"/"sell"
        if sd == "long":
            sd = "buy"
        elif sd == "short":
            sd = "sell"

    client_oid = str(int(time.time() * 1000))
    # place_limit de KucoinTrader utilise valueQty (= marge * leverage dans _value_qty())
    # Ici on n'écrase PAS la logique interne: margin_per_trade et default_leverage sont dans Settings
    # mais on peut surdimensionner en passant par place_limit_ioc/place_market si besoin.
    ok, js = t.place_limit(
        symbol=symbol,
        side=sd,
        price=float(price),
        client_oid=client_oid,
        post_only=bool(post_only),
    )

    out: Dict[str, Any] = {
        "ok": bool(ok),
        "clientOid": client_oid,
        "raw": js,
    }

    try:
        # KuCoin renvoie typiquement {"code":"200000","data":{"orderId":"..."}}
        if isinstance(js, dict):
            code = js.get("code")
            out["code"] = code
            if "data" in js and isinstance(js["data"], dict):
                data = js["data"]
                if data.get("orderId"):
                    out["orderId"] = data["orderId"]
                if data.get("clientOid") and not out.get("clientOid"):
                    out["clientOid"] = data["clientOid"]
            # certains wrappers retournent {"msg":"...","code":"100001"} pour erreurs
            if js.get("msg"):
                out["msg"] = js.get("msg")
                # si code != 200000 → force ok=False
                if str(js.get("code", "")) != "200000":
                    out["ok"] = False
    except Exception as e:
        log.warning("place_limit_order parse KO: %s", e)

    # log utile pour le debug terrain
    if out.get("ok"):
        log.info("[OK] LIMIT %s %s px=%.8f clientOid=%s orderId=%s",
                 symbol, sd, price, out.get("clientOid"), out.get("orderId"))
    else:
        log.error("[ERR] LIMIT %s %s px=%.8f clientOid=%s code=%s msg=%s raw=%s",
                  symbol, sd, price, out.get("clientOid"), out.get("code"),
                  out.get("msg"), str(out.get("raw"))[:240])

    return out
