# /app/kucoin_adapter.py
# Robust adapter to expose get_symbol_meta and common rounding/helpers
# Works with KuCoin Futures USDT-M contracts and tolerates missing fields.

from __future__ import annotations
import math
import time
from typing import Any, Dict, Optional

from kucoin_utils import fetch_all_symbols  # already in your project

# Simple in-process cache to avoid hammering the API
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

    # Expecting fetch_all_symbols() -> List[dict] with KuCoin Futures contract fields
    all_contracts = fetch_all_symbols()
    by_symbol: Dict[str, Dict[str, Any]] = {}

    for c in all_contracts or []:
        # Normalize common fields with safe defaults
        symbol = c.get("symbol") or c.get("name")
        if not symbol:
            continue

        # KuCoin Futures fields vary; try multiple keys and fall back safely
        contract_size = c.get("contractSize")
        if contract_size in (None, 0, "0", "", "null"):
            # Project requirement: default to 1.0 if unknown
            contract_size = 1.0
        else:
            try:
                contract_size = float(contract_size)
            except Exception:
                contract_size = 1.0

        price_precision = c.get("pricePrecision")
        if price_precision is None:
            # Sometimes "priceIncrement" (tick size) is provided instead
            tick = c.get("priceIncrement") or c.get("tickSize")
            if tick:
                try:
                    # infer precision from increment like 0.001 -> 3
                    s = f"{float(tick):.12f}".rstrip("0").split(".")
                    price_precision = len(s[1]) if len(s) == 2 else 0
                except Exception:
                    price_precision = 2
            else:
                price_precision = 2  # safe default

        size_precision = (
            c.get("lotSize") or c.get("sizeIncrement") or c.get("baseIncrement")
        )
        if size_precision is not None:
            try:
                # If provided as a float step (e.g., 0.001), keep it as step
                step_size = float(size_precision)
                size_precision = None  # step wins, precision becomes n/a
            except Exception:
                step_size = None
        else:
            step_size = None

        # If we didn't get a step_size, infer it from a "volPrecision" or "sizePrecision"
        vol_precision = c.get("volPrecision") or c.get("sizePrecision")
        if step_size is None:
            step_size = _to_step_from_precision(vol_precision) or 0.001

        value_precision = c.get("valuePrecision")
        if value_precision is None:
            # KuCoin often uses 0–2 for USDT notional rounding
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

        by_symbol[symbol] = {
            "symbol": symbol,
            "baseCurrency": c.get("baseCurrency") or c.get("baseAsset") or "",
            "quoteCurrency": c.get("quoteCurrency") or c.get("quoteAsset") or "USDT",
            "contractSize": contract_size,
            "pricePrecision": int(price_precision),
            "stepSize": float(step_size),
            "valuePrecision": int(value_precision),
            "minQty": float(min_qty),
            "maxLeverage": int(max_leverage),
            "isActive": bool(c.get("enableTrading", True)),
            # Keep raw in case callers need other vendor fields
            "_raw": c,
        }

    _SYMBOLS_CACHE = by_symbol
    _SYMBOLS_CACHE_TS = now
    return by_symbol


def get_symbol_meta(symbol: str) -> Dict[str, Any]:
    """
    Returns normalized metadata for a KuCoin Futures symbol.

    Keys:
      symbol, baseCurrency, quoteCurrency, contractSize (float),
      pricePrecision (int), stepSize (float), valuePrecision (int),
      minQty (float), maxLeverage (int), isActive (bool)
    """
    if not symbol:
        raise ValueError("symbol is required")

    symbol = symbol.upper()
    table = _load_symbols()
    meta = table.get(symbol)

    if not meta:
        # Cache miss? Try refresh once.
        table = _load_symbols(force=True)
        meta = table.get(symbol)

    if not meta:
        # Final safe defaults so callers never crash
        return {
            "symbol": symbol,
            "baseCurrency": "",
            "quoteCurrency": "USDT",
            "contractSize": 1.0,          # project default when missing
            "pricePrecision": 2,
            "stepSize": 0.001,
            "valuePrecision": 2,
            "minQty": 0.001,
            "maxLeverage": 20,
            "isActive": True,
            "_raw": {},
        }

    return meta


# ---------- Common helpers your trading code likely needs ----------

def round_price(price: float, price_precision: int) -> float:
    """Round price to the given precision (e.g., 2 -> 0.01 tick)."""
    if price_precision < 0:
        return float(price)
    factor = 10 ** int(price_precision)
    return math.floor(float(price) * factor + 1e-12) / factor


def round_qty(qty: float, step_size: float) -> float:
    """Round quantity DOWN to the nearest step (lot size)."""
    return _round_down(float(qty), float(step_size))


def build_value_qty_payload(margin_usdt: float) -> Dict[str, str]:
    """
    For KuCoin Futures LIMIT orders using fixed USDT margin.
    Project requirement: always use valueQty to target fixed 20 USDT (or any value).
    """
    return {"valueQty": f"{float(margin_usdt):.8f}"}


def get_tick_size(symbol: str) -> float:
    """Return tick size derived from pricePrecision."""
    meta = get_symbol_meta(symbol)
    pp = int(meta.get("pricePrecision", 2))
    return float(10 ** (-pp))


def get_step_size(symbol: str) -> float:
    """Return lot step size (quantity increment)."""
    meta = get_symbol_meta(symbol)
    return float(meta.get("stepSize", 0.001))


def get_value_precision(symbol: str) -> int:
    meta = get_symbol_meta(symbol)
    return int(meta.get("valuePrecision", 2))


def get_price_precision(symbol: str) -> int:
    meta = get_symbol_meta(symbol)
    return int(meta.get("pricePrecision", 2))
