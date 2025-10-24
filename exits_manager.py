"""
exits_manager.py — pose des exits robustes (SL + TP reduce-only) avec vérification et retry.
- SL : posé sur 100% de la taille
- TP : si >= 2 lots, pose TP1 sur ~50% (TP2 laissé au monitor BE) ; si 1 lot, pose sur 1 lot
"""
from __future__ import annotations
import logging, time
from typing import Tuple

from kucoin_utils import get_contract_info
from kucoin_trader import (
    place_reduce_only_stop,
    place_reduce_only_tp_limit,
    list_open_orders,         # <-- helper fiable (status=open)
)

LOGGER = logging.getLogger(__name__)

# --------------------------------- Utils ---------------------------------
def _round_to_tick(x: float, tick: float) -> float:
    if tick <= 0:
        return float(x)
    steps = int(float(x) / float(tick))
    return round(steps * float(tick), 12)  # précision large (contrats à tick fin)

def _split_half(lots: int) -> Tuple[int, int]:
    """
    Retourne (tp1_lots, tp2_lots) avec une division ~50/50.
    - Garantit au moins 1 lot pour TP1 si possible.
    """
    lots = int(max(0, lots))
    if lots <= 1:
        return (max(0, lots), 0)
    a = lots // 2
    b = lots - a
    if a == 0 and lots > 0:
        a, b = 1, lots - 1
    return a, b

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

def _retry_if_needed(symbol: str, side: str,
                     sl: float, tp: float,
                     sl_lots: int, tp_lots: int,
                     sl_resp: dict, tp_resp: dict) -> Tuple[dict, dict]:
    """
    Si SL/TP non 'ok', on retente une fois. Ensuite on vérifie la présence
    d'au moins un des ordres en 'open orders' et on log l'état.
    - sl_lots : taille du SL (100% de la position initiale)
    - tp_lots : taille du TP posé (souvent ~50%)
    """
    if not sl_resp.get("ok"):
        LOGGER.warning("Retry SL for %s at %.12f (lots=%d)", symbol, sl, sl_lots)
        sl_resp = place_reduce_only_stop(symbol, side, new_stop=sl, size_lots=int(sl_lots))

    if not tp_resp.get("ok") and tp_lots > 0:
        LOGGER.warning("Retry TP1 for %s at %.12f (lots=%d)", symbol, tp, tp_lots)
        tp_resp = place_reduce_only_tp_limit(symbol, side, take_profit=tp, size_lots=int(tp_lots))

    # vérification légère: on attend 0.5s puis on regarde les open orders
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

# ------------------------------- API principale -------------------------------
def attach_exits_after_fill(symbol: str, side: str, df, entry: float, sl: float, tp: float, size_lots: int):
    """
    Pose un SL (stop reduce-only) sur 100% et un TP LIMIT reduce-only.
    - Si size_lots >= 2 : TP1 posé sur ~50% (TP2 laissé au breakeven_manager)
    - Si size_lots == 1 : TP sur 1 lot (fallback 1-lot)
    Arrondit toujours aux ticks du contrat. Re-tente si nécessaire et log l'état final.
    """
    meta = get_contract_info(symbol)
    tick = float(meta.get("tickSize", 0.01))

    sl_r = _round_to_tick(float(sl), tick)
    tp_r = _round_to_tick(float(tp), tick)

    lots_full = int(size_lots)
    tp1_lots, _tp2_lots = _split_half(lots_full)

    LOGGER.info(
        "[EXITS] %s side=%s lots=%d -> SL@%.12f | TP1: %d @ %.12f",
        symbol, side, lots_full, sl_r, tp1_lots, tp_r
    )

    # 1) SL sur la taille entière
    sl_resp = place_reduce_only_stop(symbol, side, new_stop=sl_r, size_lots=lots_full)

    # 2) TP LIMIT reduce-only
    #    - >=2 lots : TP1 sur ~50% (TP2 sera géré par le BE monitor)
    #    - 1 lot    : TP sur 1 lot
    if tp1_lots > 0:
        tp_resp = place_reduce_only_tp_limit(symbol, side, take_profit=tp_r, size_lots=tp1_lots)
    else:
        tp_resp = {"ok": False, "skipped": True, "reason": "no_tp_lots"}

    # 3) retry + contrôle basique
    sl_resp, tp_resp = _retry_if_needed(
        symbol, side,
        sl=sl_r, tp=tp_r,
        sl_lots=lots_full, tp_lots=tp1_lots,
        sl_resp=sl_resp, tp_resp=tp_resp
    )

    return sl_resp, tp_resp
