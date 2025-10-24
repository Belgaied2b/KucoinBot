"""
exits_manager.py — SL + TP1/TP2 reduce-only institutionnels, avec vérification, retry et lancement du BE monitor.
- SL : posé sur 100% de la taille
- TP1 : posé sur ~50% (ou 1 lot si impossible)
- TP2 : (optionnel) posé maintenant sur le reste, ou laissé au BE monitor
"""
from __future__ import annotations
import logging, time
from typing import Tuple, Optional, Callable

from kucoin_utils import get_contract_info
from kucoin_trader import (
    place_reduce_only_stop,
    place_reduce_only_tp_limit,
    list_open_orders,         # helper fiable (status=open)
)
from breakeven_manager import launch_breakeven_thread

LOGGER = logging.getLogger(__name__)

# ------------------------------- Utils -------------------------------
def _round_to_tick(x: float, tick: float) -> float:
    if tick <= 0:
        return float(x)
    steps = int(float(x) / float(tick))
    return round(steps * float(tick), 12)

def _split_half(lots: int) -> Tuple[int, int]:
    """Retourne (tp1_lots, tp2_lots) ~50/50, en garantissant min 1 lot pour TP1 si possible."""
    lots = int(max(0, lots))
    if lots <= 1:
        return (max(0, lots), 0)
    a = lots // 2
    b = lots - a
    if a == 0 and lots > 0:
        a, b = 1, lots - 1
    return a, b

def _retry_if_needed(symbol: str, side: str,
                     sl: float, tp_price: float,
                     sl_lots: int, tp_lots: int,
                     sl_resp: dict, tp_resp: dict) -> Tuple[dict, dict]:
    """
    Si SL/TP non 'ok', on retente une fois. Ensuite on vérifie la présence
    d'au moins un des ordres en 'open orders' et on log l'état.
    """
    if not sl_resp.get("ok"):
        LOGGER.warning("Retry SL for %s at %.12f (lots=%d)", symbol, sl, sl_lots)
        sl_resp = place_reduce_only_stop(symbol, side, new_stop=sl, size_lots=int(sl_lots))

    if not tp_resp.get("ok") and tp_lots > 0:
        LOGGER.warning("Retry TP1 for %s at %.12f (lots=%d)", symbol, tp_price, tp_lots)
        tp_resp = place_reduce_only_tp_limit(symbol, side, take_profit=tp_price, size_lots=int(tp_lots))

    # vérification légère
    time.sleep(0.5)
    try:
        oo = list_open_orders(symbol)
        n_open = len(oo)
        LOGGER.info("Open orders on %s after exits: %s", symbol, n_open)
        for o in oo[:6]:
            LOGGER.info("  - id=%s side=%s type=%s price=%s stopPrice=%s size=%s reduceOnly=%s postOnly=%s status=%s",
                        o.get("id") or o.get("orderId"),
                        o.get("side"),
                        o.get("type") or o.get("orderType"),
                        o.get("price"),
                        o.get("stopPrice"),
                        o.get("size"),
                        o.get("reduceOnly"),
                        o.get("postOnly"),
                        o.get("status"))
    except Exception as e:
        LOGGER.warning("list_open_orders failed for %s: %s", symbol, e)

    return sl_resp, tp_resp

def purge_reduce_only(symbol: str) -> None:
    """Optionnel : annule d’anciens exits si tu as un cancel. Laisser vide sinon."""
    try:
        pass
    except Exception as e:
        LOGGER.warning("purge_reduce_only(%s) error: %s", symbol, e)

# --------------------------- API principale ---------------------------
def attach_exits_after_fill(
    symbol: str,
    side: str,
    df,                    # <-- conservé pour compatibilité (ignoré)
    entry: float,
    sl: float,
    tp1: float,            # prix TP1
    tp2: Optional[float],  # prix TP2 (optionnel)
    size_lots: int,
    *,
    place_tp2_now: bool = False,              # True = poser TP2 maintenant ; False = laisser le monitor le poser
    price_tick: Optional[float] = None,       # override tick si besoin
    notifier: Optional[Callable[[str], None]] = None,  # callback (ex: Telegram)
) -> Tuple[dict, dict]:
    """
    Pose SL + TP1 (+ éventuellement TP2) et lance le monitor BE (qui déplacera SL->BE quand TP1 est rempli).
    Retourne (sl_resp, tp1_resp) pour compatibilité avec l’appelant.
    """
    meta = get_contract_info(symbol)
    tick = float(price_tick) if price_tick else float(meta.get("tickSize", 0.01))

    sl_r  = _round_to_tick(float(sl),  tick)
    tp1_r = _round_to_tick(float(tp1), tick)
    tp2_r = _round_to_tick(float(tp2), tick) if tp2 is not None else None

    lots_full = int(size_lots)
    tp1_lots, tp2_lots = _split_half(lots_full)

    LOGGER.info(
        "[EXITS] %s side=%s lots=%d -> SL@%.12f | TP1: %d @ %.12f | TP2: %s",
        symbol, side, lots_full, sl_r, tp1_lots, tp1_r,
        f"{tp2_lots} @ {tp2_r:.12f}" if (tp2_r is not None and tp2_lots > 0) else "none"
    )

    # 1) SL sur 100%
    sl_resp = place_reduce_only_stop(symbol, side, new_stop=sl_r, size_lots=lots_full)

    # 2) TP1 sur ~50% (ou 1 lot si impossible)
    tp1_resp = place_reduce_only_tp_limit(symbol, side, take_profit=tp1_r, size_lots=tp1_lots) if tp1_lots > 0 else {"ok": False, "skipped": True, "reason": "no_tp1_lots"}

    # 3) TP2 maintenant ? (ou laisser le monitor le poser à TP1)
    if place_tp2_now and (tp2_r is not None) and tp2_lots > 0:
        _ = place_reduce_only_tp_limit(symbol, side, take_profit=tp2_r, size_lots=tp2_lots)

    # 4) retry + contrôle basique pour TP1 uniquement (TP2 est optionnel ici)
    sl_resp, tp1_resp = _retry_if_needed(
        symbol, side,
        sl=sl_r, tp_price=tp1_r,
        sl_lots=lots_full, tp_lots=tp1_lots,
        sl_resp=sl_resp, tp_resp=tp1_resp
    )

    # 5) Lancer le monitor BE (déplacement SL->BE quand TP1 est rempli, et pose TP2 si on ne l'a pas posé ici)
    try:
        launch_breakeven_thread(
            symbol=symbol,
            side=side,
            entry=float(entry),
            tp1=float(tp1_r),
            tp2=float(tp2_r) if tp2_r is not None else None,
            price_tick=float(tick),
            notifier=notifier,
        )
        LOGGER.info("[EXITS] BE monitor launched for %s", symbol)
    except Exception as e:
        LOGGER.exception("[EXITS] Failed to launch BE monitor for %s: %s", symbol, e)

    # ⬅️ Compat: on retourne exactement 2 valeurs (SL, TP1)
    return sl_resp, tp1_resp
