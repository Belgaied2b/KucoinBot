"""
exits_manager.py — SL + TP1/TP2 reduce-only institutionnels, avec vérification, retry et lancement du BE monitor.
Compatibilité : attach_exits_after_fill(symbol, side, df, entry, sl, tp, size_lots, tp2=None, ...)
- SL : posé sur 100% de la taille
- TP1 : posé sur ~50% (ou 1 lot si impossible) au prix 'tp'
- TP2 : (optionnel) posé maintenant sur le reste, ou laissé au BE monitor
"""
from __future__ import annotations
import logging
import time
from typing import Tuple, Optional, Callable

from kucoin_utils import get_contract_info
from kucoin_trader import (
    place_reduce_only_stop,
    place_reduce_only_tp_limit,
    list_open_orders,  # helper fiable (status=open)
)
from breakeven_manager import launch_breakeven_thread

LOGGER = logging.getLogger(__name__)

# Anti-doublon local pour le lancement du BE monitor (30s par symbole)
_BE_LAUNCH_GUARD: dict[str, float] = {}
_BE_GUARD_TTL = 30.0  # secondes

# ------------------------------- Utils -------------------------------
def _round_to_tick(x: float, tick: float) -> float:
    """
    Normalise un prix au tick le plus proche (compliant exchange).
    On évite les erreurs binaires en travaillant en nombres entiers de ticks.
    """
    if tick <= 0:
        return float(x)
    steps = round(float(x) / float(tick))
    return round(steps * float(tick), 12)

def _split_half(lots: int) -> Tuple[int, int]:
    """Retourne (tp1_lots, tp2_lots) ~50/50 ; garantit min 1 lot pour TP1 si lots>=1."""
    lots = int(max(0, lots))
    if lots <= 1:
        return (max(0, lots), 0)
    a = lots // 2
    b = lots - a
    if a == 0:
        a, b = 1, max(0, lots - 1)
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
    time.sleep(0.6)
    try:
        oo = list_open_orders(symbol)
        n_open = len(oo)
        LOGGER.info("Open orders on %s after exits: %s", symbol, n_open)
        # Log de quelques ordres pour diagnostic
        for o in oo[:8]:
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
        # Détection explicite d'un TP1 reduce-only au bon prix
        tp1_present = False
        price_str = f"{tp_price:.12f}".rstrip("0").rstrip(".")
        for o in oo:
            if str(o.get("reduceOnly")).lower() == "true":
                otype = (o.get("type") or o.get("orderType") or "").lower()
                if "limit" in otype:
                    # price peut être str; on compare en string normalisée pour éviter float issues
                    if str(o.get("price")) == price_str:
                        tp1_present = True
                        break
        if not tp1_present and tp_lots > 0:
            LOGGER.warning("TP1 not visible in open orders for %s at %s — will rely on BE monitor to reassert if needed.",
                           symbol, price_str)
    except Exception as e:
        LOGGER.warning("list_open_orders failed for %s: %s", symbol, e)

    return sl_resp, tp_resp

def _should_launch_be(symbol: str) -> bool:
    now = time.time()
    last = _BE_LAUNCH_GUARD.get(symbol, 0.0)
    if (now - last) < _BE_GUARD_TTL:
        LOGGER.info("[EXITS] BE monitor launch guarded for %s (%.1fs remaining)",
                    symbol, _BE_GUARD_TTL - (now - last))
        return False
    _BE_LAUNCH_GUARD[symbol] = now
    return True

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
    df,                    # conservé pour compat (ignoré)
    entry: float,
    sl: float,
    tp: float,             # prix TP1 (compat historique)
    size_lots: int,
    tp2: Optional[float] = None,             # prix TP2 (optionnel)
    *,
    place_tp2_now: bool = False,             # True = poser TP2 maintenant ; False = laisser le monitor le poser
    price_tick: Optional[float] = None,      # override tick si besoin
    notifier: Optional[Callable[[str], None]] = None,  # callback (ex: Telegram)
) -> Tuple[dict, dict]:
    """
    Pose SL + TP1 (+ éventuellement TP2) et lance le monitor BE (qui déplacera SL->BE quand TP1 est rempli).
    Retourne (sl_resp, tp1_resp) pour compatibilité avec l’appelant.
    """
    meta = get_contract_info(symbol)
    tick = float(price_tick) if price_tick else float(meta.get("tickSize", 0.01))

    sl_r  = _round_to_tick(float(sl),  tick)
    tp1_r = _round_to_tick(float(tp),  tick)   # 'tp' = TP1 historique
    tp2_r = _round_to_tick(float(tp2), tick) if tp2 is not None else None

    lots_full = int(size_lots)
    tp1_lots, tp2_lots = _split_half(lots_full)

    # Safety: si, pour une raison quelconque, TP1 tombe à 0 lot alors qu'on a une position, forcer 1 lot sur TP1.
    if lots_full >= 1 and tp1_lots == 0:
        tp1_lots, tp2_lots = 1, max(0, lots_full - 1)

    LOGGER.info(
        "[EXITS] %s side=%s lots=%d -> SL@%.12f | TP1: %d @ %.12f | TP2: %s",
        symbol, side, lots_full, sl_r, tp1_lots, tp1_r,
        f"{tp2_lots} @ {tp2_r:.12f}" if (tp2_r is not None and tp2_lots > 0) else "none"
    )

    # 1) SL sur 100%
    sl_resp = place_reduce_only_stop(symbol, side, new_stop=sl_r, size_lots=lots_full)

    # 2) TP1 sur ~50% (ou 1 lot si impossible)
    if tp1_lots > 0:
        tp1_resp = place_reduce_only_tp_limit(symbol, side, take_profit=tp1_r, size_lots=tp1_lots)
    else:
        tp1_resp = {"ok": False, "skipped": True, "reason": "no_tp1_lots"}

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

    # 5) Lancer le monitor BE (déplacement SL->BE quand TP1 est rempli, et pose TP2 si non posé)
    try:
        if _should_launch_be(symbol):
            launch_breakeven_thread(
                symbol=symbol,
                side=side,
                entry=float(entry),
                tp1=float(tp1_r),
                tp2=float(tp2_r) if tp2_r is not None else None,
                price_tick=None,  # laisser le monitor lire le tick du contrat (évite les incohérences)
                notifier=notifier,
            )
            LOGGER.info("[EXITS] BE monitor launched for %s", symbol)
        else:
            LOGGER.info("[EXITS] Skipped launching BE monitor for %s (guarded)", symbol)
    except Exception as e:
        LOGGER.exception("[EXITS] Failed to launch BE monitor for %s: %s", symbol, e)

    # Compat: on retourne exactement 2 valeurs (SL, TP1)
    return sl_resp, tp1_resp
