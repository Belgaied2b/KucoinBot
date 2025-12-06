# exits_manager.py — SL + TP1/TP2 reduce-only institutionnels (Bitget)
# Compatible avec breakeven_manager Bitget.

from __future__ import annotations

import logging
import time
from typing import Tuple, Optional, Callable

from bitget_utils import get_contract_info
from bitget_trader import (
    place_reduce_only_stop,
    place_reduce_only_tp_limit,
    list_open_orders,
)
from breakeven_manager import launch_breakeven_thread

LOGGER = logging.getLogger(__name__)

try:
    from settings import MIN_TP_TICKS
except Exception:
    MIN_TP_TICKS = 1


# ================================================================
# Helpers
# ================================================================
def _round_to_tick(x: float, tick: float) -> float:
    if tick <= 0:
        return float(x)
    steps = round(float(x) / float(tick))
    return round(steps * float(tick), 12)


def _split_half(lots: int):
    """Retourne (TP1_lots, TP2_lots)"""
    lots = int(max(0, lots))
    if lots <= 1:
        return (lots, 0)
    a = lots // 2
    b = lots - a
    if a == 0:
        a, b = 1, lots - 1
    return a, b


def _ensure_demi_espace(side: str, entry: float, price: float, tick: float) -> float:
    side = side.lower()
    if side == "buy":
        if price <= entry:
            price = entry + tick
    else:
        if price >= entry:
            price = entry - tick
    return _round_to_tick(price, tick)


def _normalize_targets(side, entry, tp1, tp2, tick, min_tp_ticks):
    side = side.lower()
    t1 = float(tp1)
    t2 = float(tp2) if tp2 is not None else None

    t1 = _ensure_demi_espace(side, entry, t1, tick)
    if t2 is not None:
        t2 = _ensure_demi_espace(side, entry, t2, tick)

    min_d = max(min_tp_ticks * tick, tick)

    if side == "buy":
        if (t1 - entry) < min_d:
            t1 = _round_to_tick(entry + min_d, tick)
    else:
        if (entry - t1) < min_d:
            t1 = _round_to_tick(entry - min_d, tick)

    if t2 is not None:
        if side == "buy" and t2 < t1:
            t1, t2 = t2, t1
        elif side == "sell" and t2 > t1:
            t1, t2 = t2, t1

        if side == "buy" and (t2 - t1) < min_d:
            t2 = _round_to_tick(t1 + min_d, tick)
        elif side == "sell" and (t1 - t2) < min_d:
            t2 = _round_to_tick(t1 - min_d, tick)

    return t1, t2


# ================================================================
# API principale
# ================================================================
def purge_reduce_only(symbol: str):
    """Bitget ne nécessite pas de purge agressive → no-op sécurisé."""
    return


def attach_exits_after_fill(
    symbol: str,
    side: str,
    df,
    entry: float,
    sl: float,
    tp: float,
    size_lots: int,
    tp2: Optional[float] = None,
    *,
    place_tp2_now: bool = False,
    price_tick: Optional[float] = None,
    notifier: Optional[Callable[[str], None]] = None,
) -> Tuple[dict, dict]:

    meta = get_contract_info(symbol) or {}
    tick = float(price_tick) if price_tick else float(meta.get("tickSize", 0.001))

    side = side.lower()
    if side not in ("buy", "sell"):
        side = "buy"

    # Normalisation TP1/TP2
    tp1_norm, tp2_norm = _normalize_targets(
        side=side,
        entry=float(entry),
        tp1=float(tp),
        tp2=tp2,
        tick=tick,
        min_tp_ticks=MIN_TP_TICKS,
    )

    sl_r = _round_to_tick(sl, tick)
    tp1_r = _round_to_tick(tp1_norm, tick)
    tp2_r = _round_to_tick(tp2_norm, tick) if tp2_norm else None

    lots_full = int(size_lots)
    if lots_full <= 0:
        raise ValueError(f"[EXITS] invalid lots {lots_full}")

    tp1_lots, tp2_lots = _split_half(lots_full)

    LOGGER.info(
        "[EXITS] %s side=%s lots=%d -> SL %.12f | TP1 %d @ %.12f | TP2 %s",
        symbol, side, lots_full, sl_r, tp1_lots, tp1_r,
        f"{tp2_lots} @ {tp2_r:.12f}" if (tp2_r and tp2_lots > 0) else "none"
    )

    # ================================================================
    # 1) SL reduce-only
    # ================================================================
    try:
        sl_resp = place_reduce_only_stop(symbol, side, new_stop=sl_r, size_lots=lots_full)
    except Exception as e:
        LOGGER.exception("[EXITS] SL ERROR %s: %s", symbol, e)
        raise

    if not sl_resp.get("ok"):
        raise RuntimeError(f"[EXITS] SL not placed {symbol}")

    # ================================================================
    # 2) TP1 reduce-only
    # ================================================================
    if tp1_lots > 0:
        try:
            tp1_resp = place_reduce_only_tp_limit(symbol, side, take_profit=tp1_r, size_lots=tp1_lots)
        except Exception as e:
            LOGGER.exception("[EXITS] TP1 ERROR %s: %s", symbol, e)
            raise

        if not tp1_resp.get("ok"):
            raise RuntimeError(f"[EXITS] TP1 not placed {symbol}")
    else:
        tp1_resp = {"ok": False, "reason": "no_tp1_lots"}

    # ================================================================
    # 3) TP2 (option immédiate)
    # ================================================================
    if place_tp2_now and tp2_r and tp2_lots > 0:
        try:
            r2 = place_reduce_only_tp_limit(symbol, side, take_profit=tp2_r, size_lots=tp2_lots)
            LOGGER.info("[EXITS] TP2 placed now %s : %s", symbol, r2)
        except Exception as e:
            LOGGER.exception("[EXITS] TP2 ERROR %s: %s", symbol, e)

    # ================================================================
    # 4) Vérification légère open orders
    # ================================================================
    try:
        oo = list_open_orders(symbol) or []
        LOGGER.info("[EXITS] %s open-orders=%d", symbol, len(oo))
    except Exception as e:
        LOGGER.warning("[EXITS] verify error %s: %s", symbol, e)

    # ================================================================
    # 5) Lancer le BE monitor
    # ================================================================
    try:
        launch_breakeven_thread(
            symbol=symbol,
            side=side,
            entry=float(entry),
            tp1=float(tp1_r),
            tp2=float(tp2_r) if tp2_r is not None else None,
            price_tick=tick,
            notifier=notifier,
        )
        LOGGER.info("[EXITS] BE monitor launched %s", symbol)
    except Exception as e:
        LOGGER.exception("[EXITS] BE launch ERROR %s: %s", symbol, e)

    return sl_resp, tp1_resp
