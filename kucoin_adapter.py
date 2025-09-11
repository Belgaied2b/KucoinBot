# /app/kucoin_adapter.py
# Robust adapter to expose get_symbol_meta, place_limit_order and common rounding/helpers
# Works with KuCoin Futures USDT-M contracts and tolerates missing fields.

from __future__ import annotations
import math
import time
import os
import json
import hmac
import base64
import hashlib
from typing import Any, Dict, Optional

import httpx
from kucoin_utils import fetch_all_symbols  # already in your project

# =============================
# API Auth config
# =============================
KC_API_KEY        = os.getenv("KUCOIN_API_KEY", "")
KC_API_SECRET     = os.getenv("KUCOIN_API_SECRET", "")
KC_API_PASSPHRASE = os.getenv("KUCOIN_API_PASSPHRASE", "")
KC_BASE           = "https://api-futures.kucoin.com"

# =============================
# Symbol metadata cache
# =============================
_SYMBOLS_CACHE: Optional[Dict[str, Dict[str, Any]]] = None
_SYMBOLS_CACHE_TS: float = 0.0
_CACHE_TTL_SEC = 300  # 5 minutes


def _to_step_from_precision(precision: Optional[int]) -> Optional[float]:
    if precision is None:
        return None
    try:
        return float(f"1e-{int(precision)}")
    except Exception:
        return None


def _round_down(value: float, step: Optional[float]) -> float:
    if step is None or step <= 0:
        return float(value)
    # Avoid float drifts by using integer math
    return math.floor(value / step) * step


def _load_symbols(force: bool = False) -> Dict[str, Dict[str, Any]]:
    global _SYMBOLS_CACHE, _SYMBOLS_CACHE_TS
    now = time.time()
    if (not force) and _SYMBOLS_CACHE and (now - _SYMBOLS_CACHE_TS < _CACHE_TTL_SEC):
        return _SYMBOLS_CACHE

    all_contracts = fetch_all_symbols()
    by_symbol: Dict[str, Dict[str, Any]] = {}

    for c in all_contracts or []:
        symbol = c.get("symbol") or c.get("name")
        if not symbol:
            continue

        contract_size = c.get("contractSize")
        if contract_size in (None, 0, "0", "", "null"):
            contract_size = 1.0
        else:
            try:
                contract_size = float(contract_size)
            except Exception:
                contract_size = 1.0

        price_precision = c.get("pricePrecision")
        if price_precision is None:
            tick = c.get("priceIncrement") or c.get("tickSize")
            if tick:
                try:
                    s = f"{float(tick):.12f}".rstrip("0").split(".")
                    price_precision = len(s[1]) if len(s) == 2 else 0
                except Exception:
                    price_precision = 2
            else:
                price_precision = 2

        size_precision = (
            c.get("lotSize") or c.get("sizeIncrement") or c.get("baseIncrement")
        )
        if size_precision is not None:
            try:
                step_size = float(size_precision)
                size_precision = None
            except Exception:
                step_size = None
        else:
            step_size = None

        vol_precision = c.get("volPrecision") or c.get("sizePrecision")
        if step_size is None:
            step_size = _to_step_from_precision(vol_precision) or 0.001

        value_precision = c.get("valuePrecision")
        if value_precision is None:
            value_precision = 2

        min_qty = (
            c.get("minOrderQty")
            or c.get("minTradeSize")
            or c.get("minQty")
            or c.get("minSize")
            or 0.001
        )
        try:
            min_qty = float(min_qty)
        except Exception:
            min_qty = 0.001

        max_leverage = c.get("maxLeverage") or c.get("maxLeverageLevel") or 20
        try:
            max_leverage = int(float(max_leverage))
        except Exception:
            max_leverage = 20

        by_symbol[symbol.upper()] = {
            "symbol": symbol.upper(),
            "baseCurrency": c.get("baseCurrency") or c.get("baseAsset") or "",
            "quoteCurrency": c.get("quoteCurrency") or c.get("quoteAsset") or "USDT",
            "contractSize": contract_size,
            "pricePrecision": int(price_precision),
            "stepSize": float(step_size),
            "valuePrecision": int(value_precision),
            "minQty": float(min_qty),
            "maxLeverage": int(max_leverage),
            "isActive": bool(c.get("enableTrading", True)),
            "_raw": c,
        }

    _SYMBOLS_CACHE = by_symbol
    _SYMBOLS_CACHE_TS = now
    return by_symbol


def get_symbol_meta(symbol: str) -> Dict[str, Any]:
    if not symbol:
        raise ValueError("symbol is required")

    symbol = symbol.upper()
    table = _load_symbols()
    meta = table.get(symbol)

    if not meta:
        table = _load_symbols(force=True)
        meta = table.get(symbol)

    if not meta:
        return {
            "symbol": symbol,
            "baseCurrency": "",
            "quoteCurrency": "USDT",
            "contractSize": 1.0,
            "pricePrecision": 2,
            "stepSize": 0.001,
            "valuePrecision": 2,
            "minQty": 0.001,
            "maxLeverage": 20,
            "isActive": True,
            "_raw": {},
        }

    return meta


# =============================
# Order placement
# =============================

def _sign(req_time: int, method: str, endpoint: str, body: str = "") -> Dict[str, str]:
    str_to_sign = f"{req_time}{method}{endpoint}{body}"
    sig = base64.b64encode(
        hmac.new(KC_API_SECRET.encode("utf-8"), str_to_sign.encode("utf-8"), hashlib.sha256).digest()
    ).decode()
    passphrase = base64.b64encode(
        hmac.new(KC_API_SECRET.encode("utf-8"), KC_API_PASSPHRASE.encode("utf-8"), hashlib.sha256).digest()
    ).decode()
    return {
        "KC-API-SIGN": sig,
        "KC-API-TIMESTAMP": str(req_time),
        "KC-API-KEY": KC_API_KEY,
        "KC-API-PASSPHRASE": passphrase,
        "KC-API-KEY-VERSION": "2",
    }


def place_limit_order(symbol: str, side: str, price: float, value_usdt: float,
                      sl: Optional[float] = None, tp1: Optional[float] = None,
                      tp2: Optional[float] = None, post_only: bool = True) -> Dict[str, Any]:
    """
    Place un ordre LIMIT Futures KuCoin avec marge fixe via valueQty (ex: 20 USDT).
    """
    endpoint = "/api/v1/orders"
    url = KC_BASE + endpoint
    now = int(time.time() * 1000)

    client_oid = f"oid-{int(time.time()*1000)}"

    body = {
        "clientOid": client_oid,
        "symbol": symbol,
        "side": side.lower(),
        "type": "limit",
        "price": str(price),
        "valueQty": str(value_usdt),
        "postOnly": post_only,
    }

    body_json = json.dumps(body)
    headers = _sign(now, "POST", endpoint, body_json)

    try:
        with httpx.Client() as client:
            r = client.post(url, headers=headers, content=body_json, timeout=10)
            data = r.json()
            return {
                "ok": r.status_code == 200,
                "code": data.get("code"),
                "msg": data.get("msg"),
                "data": data.get("data"),
                "orderId": (data.get("data") or {}).get("orderId"),
                "clientOid": client_oid,
                "raw": data,
            }
    except Exception as e:
        return {"ok": False, "error": str(e), "clientOid": client_oid}


# =============================
# Helpers
# =============================

def round_price(price: float, price_precision: int) -> float:
    if price_precision < 0:
        return float(price)
    factor = 10 ** int(price_precision)
    return math.floor(float(price) * factor + 1e-12) / factor


def round_qty(qty: float, step_size: float) -> float:
    return _round_down(float(qty), float(step_size))


def build_value_qty_payload(margin_usdt: float) -> Dict[str, str]:
    return {"valueQty": f"{float(margin_usdt):.8f}"}


def get_tick_size(symbol: str) -> float:
    meta = get_symbol_meta(symbol)
    pp = int(meta.get("pricePrecision", 2))
    return float(10 ** (-pp))


def get_step_size(symbol: str) -> float:
    meta = get_symbol_meta(symbol)
    return float(meta.get("stepSize", 0.001))


def get_value_precision(symbol: str) -> int:
    meta = get_symbol_meta(symbol)
    return int(meta.get("valuePrecision", 2))


def get_price_precision(symbol: str) -> int:
    meta = get_symbol_meta(symbol)
    return int(meta.get("pricePrecision", 2))
