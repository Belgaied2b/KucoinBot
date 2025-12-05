"""
exits_manager.py — Version Desk Lead Pro
----------------------------------------
- SL reduce-only (full size)
- TP1 reduce-only (~50%)
- TP2 reduce-only (option : immédiat ou runner)
- Break-even automatique après TP1 rempli
- Support trailing : update_only=True
"""

from __future__ import annotations

import logging
import time
from typing import Tuple, Optional, Callable

from kucoin_utils import get_contract_info
from kucoin_trader import (
    place_reduce_only_stop,
    place_reduce_only_tp_limit,
    list_open_orders,
)
from breakeven_manager import launch_breakeven_thread

LOGGER = logging.getLogger(__name__)

# ---- Fallback settings ----
try:
    from settings import MIN_TP_TICKS
except Exception:
    MIN_TP_TICKS = 1


# =========================================================
# Utils
# =========================================================
def _round_to_tick(x: float, tick: float) -> float:
    if tick <= 0:
        return float(x)
    steps = round(float(x) / tick)
    return round(steps * tick, 12)


def _split_half(lots: int):
    lots = max(0, int(lots))
    if lots <= 1:
        return lots, 0
    a = lots // 2
    b = lots - a
    if a == 0:
        a, b = 1, max(0, lots - 1)
    return a, b


def _ensure_demi_espace(side: str, entry: float, price: float, tick: float):
    s = side.lower()
    p = float(price)
    e = float(entry)
    if s == "buy" and p <= e:
        p = e + tick
    elif s == "sell" and p >= e:
        p = e - tick
    return _round_to_tick(p, tick)


def _normalize_targets(side, entry, tp1, tp2, tick, min_tp_ticks):
    s = side.lower()
    e = float(entry)
    t = float(tick)
    t1 = float(tp1)
    t2 = float(tp2) if tp2 is not None else None

    # demi-espace
    t1 = _ensure_demi_espace(s, e, t1, t)
    if t2 is not None:
        t2 = _ensure_demi_espace(s, e, t2, t)

    min_dist = max(min_tp_ticks * t, t)

    # distance entry → TP1
    if s == "buy" and (t1 - e) < min_dist:
        t1 = _round_to_tick(e + min_dist, t)
    if s == "sell" and (e - t1) < min_dist:
        t1 = _round_to_tick(e - min_dist, t)

    # cohérence TP2
    if t2 is not None:
        if s == "buy" and t2 < t1:
            t1, t2 = t2, t1
        if s == "sell" and t2 > t1:
            t1, t2 = t2, t1
        if s == "buy" and (t2 - t1) < min_dist:
            t2 = _round_to_tick(t1 + min_dist, t)
        if s == "sell" and (t1 - t2) < min_dist:
            t2 = _round_to_tick(t1 - min_dist, t)

    return t1, t2


# =========================================================
# purge (no-op)
# =========================================================
def purge_reduce_only(symbol):
    return


# =========================================================
# MODE TRAILING (update_only)
# =========================================================
def _place_trailing_sl(symbol, side, sl, tick, size_lots):
    """Place un SL reduce-only, sans toucher TP1/TP2 ou BE."""
    sl_r = _round_to_tick(float(sl), tick)
    LOGGER.info(f"[EXITS] TRAILING: {symbol} new reduce-only SL = {sl_r}")

    try:
        resp = place_reduce_only_stop(
            symbol,
            side,
            new_stop=sl_r,
            size_lots=size_lots
        )
        if not resp.get("ok"):
            raise RuntimeError(f"SL trailing non ok: {resp}")
        return resp
    except Exception as e:
        LOGGER.error(f"[EXITS] trailing SL failed: {e}")
        raise


# =========================================================
# MAIN FUNCTION
# =========================================================
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
    notifier: Optional[Callable] = None,
    update_only: bool = False,
):
    """
    SL + TP1 + TP2 + BreakEven
    Mode trailing → update_only=True (ne place qu’un nouveau SL).
    """

    # -------------------------------------------
    # INFO CONTRAT
    # -------------------------------------------
    meta = get_contract_info(symbol) or {}
    tick = float(price_tick if price_tick else meta.get("tickSize", 0.01))

    lots_full = int(size_lots)
    if lots_full <= 0:
        raise ValueError("Taille invalide")

    side = side.lower()

    # -------------------------------------------
    # MODE TRAILING: ne place QUE le SL
    # -------------------------------------------
    if update_only:
        resp_sl = _place_trailing_sl(symbol, side, sl, tick, lots_full)
        return resp_sl, {"ok": False, "reason": "update_only_no_tp"}

    # -------------------------------------------
    # MODE COMPLET
    # -------------------------------------------
    tp1_norm, tp2_norm = _normalize_targets(
        side, entry, tp, tp2, tick, MIN_TP_TICKS
    )

    sl_r = _round_to_tick(sl, tick)
    tp1_r = _round_to_tick(tp1_norm, tick)
    tp2_r = _round_to_tick(tp2_norm, tick) if tp2_norm else None

    tp1_lots, tp2_lots = _split_half(lots_full)

    LOGGER.info(
        f"[EXITS] {symbol} side={side} entry={entry} "
        f"SL={sl_r} | TP1 {tp1_lots}@{tp1_r} | "
        f"TP2 {tp2_lots}@{tp2_r if tp2_r else 'none'}"
    )

    # -------------------------------------------
    # 1) SL FULL reduce-only
    # -------------------------------------------
    sl_resp = place_reduce_only_stop(
        symbol, side, new_stop=sl_r, size_lots=lots_full
    )
    if not sl_resp.get("ok"):
        LOGGER.error(f"[EXITS] SL non ok: {sl_resp}")
        raise RuntimeError("SL not placed")

    # -------------------------------------------
    # 2) TP1 reduce-only
    # -------------------------------------------
    if tp1_lots > 0:
        tp1_resp = place_reduce_only_tp_limit(
            symbol,
            side,
            take_profit=tp1_r,
            size_lots=tp1_lots,
        )
        if not tp1_resp.get("ok"):
            LOGGER.error(f"[EXITS] TP1 non ok: {tp1_resp}")
            raise RuntimeError("TP1 not placed")
    else:
        tp1_resp = {"ok": False, "reason": "no_lots"}

    # -------------------------------------------
    # 3) TP2 immédiat (option)
    # -------------------------------------------
    if place_tp2_now and tp2_r and tp2_lots > 0:
        try:
            place_reduce_only_tp_limit(
                symbol,
                side,
                take_profit=tp2_r,
                size_lots=tp2_lots
            )
        except Exception as e:
            LOGGER.warning(f"[EXITS] TP2 placement failed: {e}")

    # -------------------------------------------
    # 4) Vérification légère hors API
    # -------------------------------------------
    try:
        time.sleep(0.3)
        open_orders = list_open_orders(symbol) or []
        LOGGER.info(f"[EXITS] Orders after placement ({len(open_orders)}):")
        for o in open_orders[:6]:
            LOGGER.info(str(o))
    except Exception as e:
        LOGGER.warning(f"[EXITS] Impossible de lister les ordres: {e}")

    # -------------------------------------------
    # 5) Lancer le monitor BreakEven
    # -------------------------------------------
    try:
        launch_breakeven_thread(
            symbol=symbol,
            side=side,
            entry=entry,
            tp1=tp1_r,
            tp2=tp2_r,
            price_tick=tick,
            notifier=notifier,
        )
        LOGGER.info(f"[EXITS] BreakEven monitor launched for {symbol}")
    except Exception as e:
        LOGGER.error(f"[EXITS] BE thread failed: {e}")

    return sl_resp, tp1_resp
