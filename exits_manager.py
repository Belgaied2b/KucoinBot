"""
exits_manager.py — SL + TP1/TP2 reduce-only institutionnels, avec vérification légère
et lancement du monitor break-even.

Compatibilité : attach_exits_after_fill(symbol, side, df, entry, sl, tp, size_lots, tp2=None, ...)
- SL  : posé sur 100% de la taille (reduce-only)
- TP1 : posé sur ~50% (ou 1 lot si impossible) au prix "tp" (normalisé, bon côté de l'entry)
- TP2 : (optionnel) cible plus lointaine ; peut être posée maintenant ou laissée au monitor BE
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

# ---- Petites bornes (fallbacks si pas dans settings.py) ----
try:  # écart minimal TP / entry en ticks
    from settings import MIN_TP_TICKS  # type: ignore
except Exception:
    MIN_TP_TICKS = 1


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


def _ensure_demi_espace(side: str, entry: float, price: float, tick: float) -> float:
    """
    Force un TP dans le “bon demi-espace” par rapport à l'entry:
      - buy : TP > entry
      - sell: TP < entry
    Avec au moins 1 tick d'écart.
    """
    s = (side or "").lower()
    e = float(entry)
    p = float(price)
    t = float(tick)
    if s == "buy":
        if p <= e:
            p = e + max(t, 0.0)
    else:
        if p >= e:
            p = e - max(t, 0.0)
    return _round_to_tick(p, t)


def _normalize_targets(
    *,
    side: str,
    entry: float,
    tp1: float,
    tp2: Optional[float],
    tick: float,
    min_tp_ticks: int,
) -> Tuple[float, Optional[float]]:
    """
    Normalise TP1/TP2 pour éviter les inversions et garantir un écart mini:
      - TP1 dans le bon demi-espace, à au moins min_tp_ticks d'entry.
      - Si TP2 fourni:
          * côté buy  : entry < TP1 <= TP2
          * côté sell : entry > TP1 >= TP2
      - Si TP2 absent: TP1 seulement, mais avec distance mini en ticks.
    """
    s = (side or "").lower()
    e = float(entry)
    t1 = float(tp1)
    t2 = float(tp2) if tp2 is not None else None
    t = float(tick)

    # 1) demi-espace
    t1 = _ensure_demi_espace(s, e, t1, t)
    if t2 is not None:
        t2 = _ensure_demi_espace(s, e, t2, t)

    # 2) distance mini entry -> TP1
    min_d = max(int(min_tp_ticks) * t, t if t > 0 else 0.0)
    if s == "buy":
        if (t1 - e) < min_d:
            t1 = _round_to_tick(e + min_d, t)
    else:
        if (e - t1) < min_d:
            t1 = _round_to_tick(e - min_d, t)

    # 3) cohérence TP1/TP2 si TP2 fourni
    if t2 is not None:
        if s == "buy":
            if t2 < t1:
                t1, t2 = t2, t1
        else:
            if t2 > t1:
                t1, t2 = t2, t1

        # léger écart mini TP1/TP2
        if s == "buy" and (t2 - t1) < min_d:
            t2 = _round_to_tick(t1 + min_d, t)
        elif s == "sell" and (t1 - t2) < min_d:
            t2 = _round_to_tick(t1 - min_d, t)

    return t1, t2


# --------------------------- API utilitaire ---------------------------
def purge_reduce_only(symbol: str) -> None:
    """
    Optionnel : annule d’anciens exits si tu as un cancel manuel.

    Pour l'instant, on ne fait rien ici pour éviter de toucher à des ordres
    existants. La logique d'annulation peut être implémentée plus tard
    (via list_open_orders + cancel_order) si besoin.
    """
    try:
        # No-op volontaire (on ne touche pas aux exits existants).
        return
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
    Pose SL + TP1 (+ éventuellement TP2) et lance le monitor BE (qui déplacera SL->BE
    quand TP1 est rempli).

    Retourne (sl_resp, tp1_resp) pour compatibilité avec l’appelant.
    """
    meta = get_contract_info(symbol) or {}
    tick = float(price_tick) if price_tick else float(meta.get("tickSize", 0.01) or 0.01)
    side = (side or "").lower()
    if side not in ("buy", "sell"):
        LOGGER.warning("[EXITS] %s side invalide '%s', fallback 'buy'", symbol, side)
        side = "buy"

    # --- Normalisation TP1/TP2 ---
    tp1_norm, tp2_norm = _normalize_targets(
        side=side,
        entry=float(entry),
        tp1=float(tp),
        tp2=tp2,
        tick=tick,
        min_tp_ticks=int(MIN_TP_TICKS),
    )

    sl_r = _round_to_tick(float(sl), tick)
    tp1_r = _round_to_tick(float(tp1_norm), tick)
    tp2_r = _round_to_tick(float(tp2_norm), tick) if tp2_norm is not None else None

    lots_full = int(size_lots)
    if lots_full <= 0:
        raise ValueError(f"[EXITS] lots invalides pour {symbol}: {lots_full}")

    tp1_lots, tp2_lots = _split_half(lots_full)
    if lots_full >= 1 and tp1_lots == 0:
        tp1_lots, tp2_lots = 1, max(0, lots_full - 1)

    LOGGER.info(
        "[EXITS] %s side=%s lots=%d -> SL@%.12f | TP1: %d @ %.12f | TP2: %s",
        symbol,
        side,
        lots_full,
        sl_r,
        tp1_lots,
        tp1_r,
        f"{tp2_lots} @ {tp2_r:.12f}" if (tp2_r is not None and tp2_lots > 0) else "none",
    )

    # =======================
    # 1) SL reduce-only FULL
    # =======================
    sl_resp: dict
    try:
        sl_resp = place_reduce_only_stop(
            symbol,
            side,
            new_stop=sl_r,
            size_lots=lots_full,
        )
    except Exception as e:
        LOGGER.exception("[EXITS] place_reduce_only_stop failed on %s: %s", symbol, e)
        raise

    if not sl_resp or not sl_resp.get("ok"):
        LOGGER.error("[EXITS] SL placement not ok on %s: %s", symbol, sl_resp)
        raise RuntimeError(f"SL not placed correctly on {symbol}")

    # ==========================
    # 2) TP1 reduce-only (split)
    # ==========================
    tp1_resp: dict
    if tp1_lots > 0:
        try:
            tp1_resp = place_reduce_only_tp_limit(
                symbol,
                side,
                take_profit=tp1_r,
                size_lots=tp1_lots,
            )
        except Exception as e:
            LOGGER.exception("[EXITS] place_reduce_only_tp_limit(TP1) failed on %s: %s", symbol, e)
            raise

        if not tp1_resp or not tp1_resp.get("ok"):
            LOGGER.error("[EXITS] TP1 placement not ok on %s: %s", symbol, tp1_resp)
            raise RuntimeError(f"TP1 not placed correctly on {symbol}")
    else:
        tp1_resp = {"ok": False, "reason": "no_lots_for_tp1"}

    # ==========================
    # 3) TP2 immédiat (option)
    # ==========================
    if place_tp2_now and tp2_r is not None and tp2_lots > 0:
        try:
            tp2_resp = place_reduce_only_tp_limit(
                symbol,
                side,
                take_profit=tp2_r,
                size_lots=tp2_lots,
            )
            LOGGER.info(
                "[EXITS] TP2 placed for %s: lots=%d @ %.12f -> resp=%s",
                symbol,
                tp2_lots,
                tp2_r,
                tp2_resp,
            )
        except Exception as e:
            LOGGER.exception("[EXITS] place_reduce_only_tp_limit(TP2) failed on %s: %s", symbol, e)
            # on ne raise pas ici : SL+TP1 sont déjà en place

    # ==========================
    # 4) Vérification light
    # ==========================
    try:
        max_tries = 3
        last_oo = []
        for attempt in range(1, max_tries + 1):
            time.sleep(0.4 * attempt)
            try:
                oo = list_open_orders(symbol) or []
                last_oo = oo
            except Exception as e:
                LOGGER.warning(
                    "[EXITS] list_open_orders error on %s try %d/%d: %s",
                    symbol,
                    attempt,
                    max_tries,
                    e,
                )
                continue

            if not isinstance(oo, list):
                break

            # on log et on sort si on voit au moins 1 SL et 1 TP reduce-only
            has_sl = False
            has_tp = False
            for o in oo:
                try:
                    ro = str(o.get("reduceOnly") or "").lower() == "true"
                    o_type = (o.get("type") or o.get("orderType") or "").lower()
                    if not ro:
                        continue
                    if "stop" in o_type:
                        has_sl = True
                    elif "limit" in o_type or "take" in o_type:
                        has_tp = True
                except Exception:
                    continue

            if has_sl and has_tp:
                break

        n_open = len(last_oo) if isinstance(last_oo, list) else 0
        LOGGER.info("[EXITS] Open reduce-only orders on %s after exits: %s", symbol, n_open)
        for o in (last_oo[:8] if isinstance(last_oo, list) else []):
            try:
                LOGGER.info(
                    "  - id=%s side=%s type=%s price=%s stopPrice=%s size=%s "
                    "reduceOnly=%s postOnly=%s status=%s",
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
            except Exception:
                continue
    except Exception as e:
        LOGGER.warning("[EXITS] open-orders verification failed for %s: %s", symbol, e)

    # ==========================
    # 5) Lancer le monitor BE
    # ==========================
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

    # Compat: on retourne exactement 2 valeurs (SL, TP1)
    return sl_resp, tp1_resp
