"""
exits_manager.py — pose des exits robustes (SL + TP1/TP2 reduce-only) puis lancement du monitor BE.
- SL: stop reduce-only sur la TAILLE ENTIÈRE
- TP1: limit reduce-only sur ~50% (arrondi, min 1 lot)
- TP2: limit reduce-only sur le reste (optionnel: peut être posé plus tard)
- Lancement du monitor: déplace SL -> BE lorsque TP1 est rempli et (si besoin) pose TP2
- Tous les prix sont arrondis au tickSize du contrat
"""
from __future__ import annotations
import logging, time
from typing import Optional, Tuple, Callable, Dict, Any

from kucoin_utils import get_contract_info
from kucoin_trader import (
    place_reduce_only_stop,
    place_reduce_only_tp_limit,
    list_open_orders,
)
from breakeven_manager import launch_breakeven_thread

LOGGER = logging.getLogger(__name__)

# -------- utils --------
def _round_to_tick(x: float, tick: float) -> float:
    if tick <= 0:
        return float(x)
    steps = int(float(x) / float(tick))
    return round(steps * float(tick), 12)  # précision large, on affiche côté exchange

def _half_split(lots: int) -> Tuple[int, int]:
    """Retourne (tp1_lots, tp2_lots) en s'assurant min 1 lot pour TP1 si possible."""
    lots = int(max(0, lots))
    if lots <= 1:
        return (1 if lots == 1 else 0, 0)
    a = lots // 2
    b = lots - a
    # on préfère donner 1 lot à TP1 si a == 0
    if a == 0 and lots > 0:
        a, b = 1, lots - 1
    return a, b

def _log_open_orders(symbol: str) -> None:
    try:
        time.sleep(0.25)
        items = list_open_orders(symbol)
        LOGGER.info("Open orders on %s after exits: %d", symbol, len(items))
        for o in items[:6]:
            LOGGER.info(
                "  - id=%s side=%s type=%s price=%s stopPrice=%s size=%s reduceOnly=%s postOnly=%s status=%s",
                o.get("id") or o.get("orderId"),
                o.get("side"),
                o.get("type") or o.get("orderType"),
                o.get("price"),
                o.get("stopPrice"),
                o.get("size"),
                o.get("reduceOnly"),
                o.get("postOnly"),
                o.get("status"),
            )
    except Exception as e:
        LOGGER.warning("Failed to list open orders for %s: %s", symbol, e)

# -------- API principale --------
def attach_exits_after_fill(
    symbol: str,
    side: str,
    entry: float,
    sl: float,
    tp1: float,
    tp2: Optional[float],
    size_lots: int,
    *,
    place_tp2_now: bool = True,
    price_tick: Optional[float] = None,
    notifier: Optional[Callable[[str], None]] = None,
) -> Dict[str, Any]:
    """
    Pose SL + TP1 (+ TP2 si demandé) puis lance le monitor BE.

    notifier(msg): callback optionnel (ex: envoi Telegram)
    place_tp2_now:
        - True  -> pose TP2 tout de suite (recommandé)
        - False -> le monitor posera TP2 au moment de TP1 (si absent)

    Retourne un dict avec les réponses SL/TP1/TP2.
    """
    meta = get_contract_info(symbol)
    tick = float(price_tick) if price_tick else float(meta.get("tickSize", 0.01))

    sl_r  = _round_to_tick(float(sl),  tick)
    tp1_r = _round_to_tick(float(tp1), tick)
    tp2_r = _round_to_tick(float(tp2), tick) if tp2 is not None else None

    tp1_lots, tp2_lots = _half_split(int(size_lots))
    # sécurité: si 0 lot TP1 (cas pathologique), tout sur TP2
    if tp1_lots == 0 and tp2_lots > 0:
        tp1_lots, tp2_lots = 1, max(0, int(size_lots) - 1)

    LOGGER.info(
        "[EXITS] %s side=%s lots=%d -> SL@%.12f | TP1: %d @ %.12f | TP2: %s",
        symbol, side, int(size_lots), sl_r, tp1_lots, tp1_r,
        f"{tp2_lots} @ {tp2_r:.12f}" if tp2_r is not None and tp2_lots > 0 else "none",
    )

    # 1) SL sur la taille entière
    sl_resp = place_reduce_only_stop(symbol, side, new_stop=sl_r, size_lots=int(size_lots))

    # 2) TP1 sur ~50% (reduce-only limit)
    tp1_resp = place_reduce_only_tp_limit(symbol, side, take_profit=tp1_r, size_lots=int(tp1_lots))

    # 3) (Option) TP2 posé maintenant pour le reste
    if place_tp2_now and tp2_r is not None and tp2_lots > 0:
        tp2_resp = place_reduce_only_tp_limit(symbol, side, take_profit=tp2_r, size_lots=int(tp2_lots))
    else:
        tp2_resp = {"ok": False, "skipped": True, "reason": "deferred_to_BE_monitor" if tp2_r else "no_tp2_price_or_lots"}

    _log_open_orders(symbol)

    # 4) Lancer le monitor BE :
    #    - détecte le remplissage de TP1 (via baisse de taille)
    #    - déplace SL -> BE pour le reste
    #    - si TP2 n'a pas été posé ici, il le posera à TP1
    try:
        launch_breakeven_thread(
            symbol=symbol,
            side=side,
            entry=float(entry),
            tp1=float(tp1_r),
            tp2=float(tp2_r) if tp2_r is not None else None,
            price_tick=float(tick),
            notifier=notifier,  # <- enverra une alerte Telegram quand TP1 est pris et SL->BE
        )
        LOGGER.info("[EXITS] BE monitor launched for %s", symbol)
    except Exception as e:
        LOGGER.exception("[EXITS] Failed to launch BE monitor for %s: %s", symbol, e)

    return {"sl": sl_resp, "tp1": tp1_resp, "tp2": tp2_resp}
