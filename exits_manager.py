"""
exits_manager.py — SL + TP1/TP2 reduce-only institutionnels, avec vérification, retry et lancement du BE monitor.
Compatibilité : attach_exits_after_fill(symbol, side, df, entry, sl, tp, size_lots, tp2=None, ...)
- SL : posé sur 100% de la taille
- TP1 : posé sur ~50% (ou 1 lot si impossible) au prix 'tp' (normalisé = plus proche de l'entry)
- TP2 : (optionnel) la cible plus lointaine ; peut être posée maintenant ou via le BE monitor
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
from stops import get_last_sl_source  # <<< nouvelle import pour tracer la source du SL

# (optionnel) si tu utilises le module exits.py (stopOrders natif)
try:
    from exits import place_take_profit as place_tp_fixed, place_stop_loss as place_sl_fixed
    USE_FIXED_TPSL = True
except Exception:
    place_tp_fixed = None  # type: ignore
    place_sl_fixed = None  # type: ignore
    USE_FIXED_TPSL = False

LOGGER = logging.getLogger(__name__)

# Anti-doublon local pour le lancement du BE monitor (clé par position)
_BE_LAUNCH_GUARD: dict[str, float] = {}
_BE_GUARD_TTL = 30.0  # secondes

# ------------------------------- Utils -------------------------------
def _round_to_tick(x: float, tick: float) -> float:
    """Normalise un prix au tick le plus proche (compliant exchange)."""
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

def _normalize_targets(side: str, entry: float, tp1: float, tp2: Optional[float]) -> Tuple[float, Optional[float], bool]:
    """
    Garantit : TP1 = target la plus proche de l'entry dans le bon sens ; TP2 = plus lointaine.
    Retourne (tp1_norm, tp2_norm, swapped) ; swapped=True si on a inversé les valeurs fournies.
    """
    s = (side or "").lower()
    e = float(entry)
    t1 = float(tp1)
    t2 = float(tp2) if tp2 is not None else None

    # Si pas de TP2 → rien à normaliser
    if t2 is None:
        return t1, None, False

    if s == "buy":
        # On veut t1 > e et t2 > e ; le plus proche de e = TP1
        candidates = [x for x in (t1, t2) if x > e]
        if len(candidates) == 2:
            near = min(candidates, key=lambda x: x - e)
            far  = max(candidates, key=lambda x: x - e)
            swapped = not (near == t1 and far == t2)
            return near, far, swapped
        # Si l’un est du mauvais côté, on garde l’ordre relatif mais on loggue
        return t1, t2, False
    else:
        # sell : on veut t1 < e et t2 < e ; le plus proche (en valeur absolue) = TP1
        candidates = [x for x in (t1, t2) if x < e]
        if len(candidates) == 2:
            near = max(candidates, key=lambda x: x)  # plus proche de e par le haut
            far  = min(candidates, key=lambda x: x)  # plus lointain
            swapped = not (near == t1 and far == t2)
            return near, far, swapped
        return t1, t2, False

def _retry_if_needed(symbol: str, side: str,
                     sl: float, tp_price: float,
                     sl_lots: int, tp_lots: int,
                     sl_resp: dict, tp_resp: dict) -> Tuple[dict, dict]:
    """
    Si SL/TP non 'ok', on retente une fois. Ensuite on vérifie la présence
    d'au moins un des ordres via open orders, avec backoff tolérant (anti-404/latence d'indexation).
    """
    if not sl_resp.get("ok"):
        LOGGER.warning("Retry SL for %s at %.12f (lots=%d)", symbol, sl, sl_lots)
        sl_resp = place_reduce_only_stop(symbol, side, new_stop=sl, size_lots=int(sl_lots))

    if not tp_resp.get("ok") and tp_lots > 0:
        LOGGER.warning("Retry TP1 for %s at %.12f (lots=%d)", symbol, tp_price, tp_lots)
        tp_resp = place_reduce_only_tp_limit(symbol, side, take_profit=tp_price, size_lots=int(tp_lots))

    # Backoff progressif pour laisser KuCoin indexer
    max_tries = 5
    oo = []
    for attempt in range(1, max_tries + 1):
        time.sleep(0.6 * attempt)
        try:
            res = list_open_orders(symbol)
            if isinstance(res, list):
                oo = res
                if len(oo) > 0 or attempt == max_tries:
                    break
        except Exception:
            pass

    try:
        n_open = len(oo) if isinstance(oo, list) else 0
        LOGGER.info("Open orders on %s after exits: %s (after %d tries)", symbol, n_open, attempt)
        for o in (oo[:8] if isinstance(oo, list) else []):
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
        # Note: on ne “force” rien ici, le BE monitor re-assert si besoin.
    except Exception as e:
        LOGGER.warning("open-orders verification failed for %s: %s", symbol, e)

    return sl_resp, tp_resp

# --------- Guard BE par position (clé = symbol + entry arrondi au tick) ---------
def _be_guard_key(symbol: str, entry: float, tick: float) -> str:
    return f"{symbol}#{_round_to_tick(float(entry), float(tick))}"

def _should_launch_be(symbol: str, entry: float, tick: float) -> bool:
    key = _be_guard_key(symbol, entry, tick)
    now = time.time()
    last = _BE_LAUNCH_GUARD.get(key, 0.0)
    if (now - last) < _BE_GUARD_TTL:
        LOGGER.info("[EXITS] BE monitor launch guarded for %s (%.1fs remaining)",
                    key, _BE_GUARD_TTL - (now - last))
        return False
    _BE_LAUNCH_GUARD[key] = now
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
    tp: float,             # prix TP1 (hist.) — sera normalisé avec tp2 ci-dessous
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

    # --- 0) Normalisation des targets (clé pour éviter TP1/TP2 inversés) ---
    tp_norm, tp2_norm, swapped = _normalize_targets(side, float(entry), float(tp), tp2 if tp2 is not None else None)
    if swapped:
        LOGGER.warning("[EXITS] %s side=%s — TP1/TP2 reçus inversés. Normalisation: tp1=%s -> %.12f | tp2=%s -> %.12f",
                       symbol, side, f"{tp:.12f}", tp_norm, f"{tp2:.12f}" if tp2 is not None else "None",
                       tp2_norm if tp2_norm is not None else float("nan"))

    sl_r  = _round_to_tick(float(sl),      tick)
    tp1_r = _round_to_tick(float(tp_norm), tick)     # TP1 = plus proche de l'entry
    tp2_r = _round_to_tick(float(tp2_norm), tick) if tp2_norm is not None else None

    lots_full = int(size_lots)
    tp1_lots, tp2_lots = _split_half(lots_full)
    if lots_full >= 1 and tp1_lots == 0:
        tp1_lots, tp2_lots = 1, max(0, lots_full - 1)

    # --- LOG preview + source du SL (nouveau) ---
    try:
        sl_src = get_last_sl_source()
    except Exception:
        sl_src = "unknown"

    LOGGER.info(
        "[EXITS] %s side=%s lots=%d -> SL@%.12f (src=%s) | TP1: %d @ %.12f | TP2: %s",
        symbol, side, lots_full, sl_r, sl_src, tp1_lots, tp1_r,
        f"{tp2_lots} @ {tp2_r:.12f}" if (tp2_r is not None and tp2_lots > 0) else "none"
    )

    # 1) SL sur 100%
    if USE_FIXED_TPSL and place_sl_fixed:
        sl_resp = place_sl_fixed(symbol, side, size_lots=lots_full, stop_price=sl_r)
        if not sl_resp.get("ok"):
            LOGGER.warning("[EXITS] /stopOrders SL refusé -> fallback reduce-only /orders")
            sl_resp = place_reduce_only_stop(symbol, side, new_stop=sl_r, size_lots=lots_full)
    else:
        sl_resp = place_reduce_only_stop(symbol, side, new_stop=sl_r, size_lots=lots_full)

    # 2) TP1 sur ~50% (ou 1 lot si impossible)
    if tp1_lots > 0:
        if USE_FIXED_TPSL and place_tp_fixed:
            tp1_resp = place_tp_fixed(symbol, side, size_lots=tp1_lots, tp_price=tp1_r)
            if not tp1_resp.get("ok"):
                LOGGER.warning("[EXITS] /stopOrders TP1 refusé -> fallback reduce-only /orders")
                tp1_resp = place_reduce_only_tp_limit(symbol, side, take_profit=tp1_r, size_lots=tp1_lots)
        else:
            tp1_resp = place_reduce_only_tp_limit(symbol, side, take_profit=tp1_r, size_lots=tp1_lots)
    else:
        tp1_resp = {"ok": False, "skipped": True, "reason": "no_tp1_lots"}

    # 3) TP2 maintenant ? (ou laisser le monitor le poser à TP1)
    if place_tp2_now and (tp2_r is not None) and tp2_lots > 0:
        if USE_FIXED_TPSL and place_tp_fixed:
            tp2_resp = place_tp_fixed(symbol, side, size_lots=tp2_lots, tp_price=tp2_r)
            if not tp2_resp.get("ok"):
                LOGGER.warning("[EXITS] /stopOrders TP2 refusé -> fallback reduce-only /orders")
                _ = place_reduce_only_tp_limit(symbol, side, take_profit=tp2_r, size_lots=tp2_lots)
        else:
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
        now_ok = _should_launch_be(symbol, float(entry), float(tick))   # guard par position (symbol+entry~tick)
        if now_ok:
            launch_breakeven_thread(
                symbol=symbol,
                side=side,
                entry=float(entry),
                tp1=float(tp1_r),                                    # on passe le TP1 NORMALISÉ
                tp2=float(tp2_r) if tp2_r is not None else None,
                price_tick=float(tick),                               # tick au monitor
                notifier=notifier,
            )
            LOGGER.info("[EXITS] BE monitor launched for %s", symbol)
        else:
            LOGGER.info("[EXITS] Skipped launching BE monitor for %s (guarded)", symbol)
    except Exception as e:
        LOGGER.exception("[EXITS] Failed to launch BE monitor for %s: %s", symbol, e)

    # Compat: on retourne exactement 2 valeurs (SL, TP1)
    return sl_resp, tp1_resp
