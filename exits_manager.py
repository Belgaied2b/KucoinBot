"""
exits_manager.py — pose des exits robustes (SL + TP reduce-only) avec vérification et retry.
"""
from __future__ import annotations
import logging, time
from typing import Tuple

from kucoin_utils import get_contract_info
from kucoin_trader import (
    place_reduce_only_stop,
    place_reduce_only_tp_limit,
    list_open_orders,         # <-- nouveau helper
)
LOGGER = logging.getLogger(__name__)

def _round_to_tick(x: float, tick: float) -> float:
    if tick <= 0: 
        return float(x)
    steps = int(float(x) / float(tick))
    return round(steps * float(tick), 8)

def purge_reduce_only(symbol: str) -> None:
    """
    Si tu avais déjà une fonction de purge, garde-la.
    Sinon, laisse vide: côté KuCoin, on utilise reduce-only, donc pas de conflits
    (les vieux exits peuvent rester si la position est à 0, ils ne s'exécuteront pas).
    """
    try:
        # Optionnel: appeler ton cancel si tu en as un.
        pass
    except Exception as e:
        LOGGER.warning("purge_reduce_only(%s) error: %s", symbol, e)

def _retry_if_needed(symbol: str, side: str, sl: float, tp: float, lots: int,
                     sl_resp: dict, tp_resp: dict) -> Tuple[dict, dict]:
    """
    Si SL/TP non 'ok', on retente une fois. Ensuite on vérifie la présence
    d'au moins un des ordres en 'open orders' et on log l'état.
    """
    if not (sl_resp.get("ok")):
        LOGGER.warning("Retry SL for %s at %.8f", symbol, sl)
        sl_resp = place_reduce_only_stop(symbol, side, new_stop=sl, size_lots=lots)

    if not (tp_resp.get("ok")):
        LOGGER.warning("Retry TP for %s at %.8f", symbol, tp)
        tp_resp = place_reduce_only_tp_limit(symbol, side, take_profit=tp, size_lots=lots)

    # vérification légère: on attend 0.5s puis on regarde les open orders
    time.sleep(0.5)
    try:
        oo = list_open_orders(symbol)
        n_open = len(oo)
        LOGGER.info("Open orders on %s after exits: %s", symbol, n_open)
    except Exception as e:
        LOGGER.warning("list_open_orders failed for %s: %s", symbol, e)

    return sl_resp, tp_resp

def attach_exits_after_fill(symbol: str, side: str, df, entry: float, sl: float, tp: float, size_lots: int):
    """
    Pose un SL (stop market reduce-only) et un TP (limit reduce-only).
    Arrondit toujours aux ticks du contrat.
    Re-tente si nécessaire et log l'état final.
    """
    meta = get_contract_info(symbol)
    tick = float(meta.get("tickSize", 0.01))
    sl_r = _round_to_tick(sl, tick)
    tp_r = _round_to_tick(tp, tick)

    # 1) SL + TP
    sl_resp = place_reduce_only_stop(symbol, side, new_stop=sl_r, size_lots=int(size_lots))
    tp_resp = place_reduce_only_tp_limit(symbol, side, take_profit=tp_r, size_lots=int(size_lots))

    # 2) retry + contrôle basique
    sl_resp, tp_resp = _retry_if_needed(symbol, side, sl_r, tp_r, int(size_lots), sl_resp, tp_resp)

    return sl_resp, tp_resp
