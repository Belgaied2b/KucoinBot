# breakeven_manager.py
import time, logging, threading
from typing import Optional, Callable

from kucoin_utils import get_contract_info
from kucoin_trader import (
    modify_stop_order,
    get_open_position,
    get_mark_price,                      # fallback markPrice public (pour logs/info)
    place_reduce_only_tp_limit,          # pour poser TP2 si absent
    list_open_orders,                    # pour détecter TP2 existant
)

LOGGER = logging.getLogger(__name__)

def _notify_wrap(notifier: Optional[Callable[[str], None]], msg: str):
    if notifier:
        try:
            notifier(msg)
        except Exception:
            pass
    LOGGER.info(msg)

def _has_open_tp_at_price(symbol: str, side: str, price: float, tick: float) -> bool:
    """Vérifie s'il existe déjà un TP LIMIT reduce-only à ~ce prix (±0.5 tick)."""
    try:
        items = list_open_orders(symbol)
        if not items:
            return False
        opp_side = "sell" if side.lower() == "buy" else "buy"
        tol = max(tick * 0.5, 0.0)
        pmin, pmax = (price - tol, price + tol)
        for o in items:
            otype = (o.get("type") or o.get("orderType") or "").lower()
            o_side = (o.get("side") or "").lower()
            ro = bool(o.get("reduceOnly"))
            p = o.get("price")
            if p is None:
                continue
            try:
                op = float(p)
            except Exception:
                continue
            if otype == "limit" and ro and o_side == opp_side and pmin <= op <= pmax:
                return True
    except Exception:
        return False
    return False


def monitor_breakeven(
    symbol: str,
    side: str,                               # 2e arg
    entry: float,
    tp1: float,
    tp2: Optional[float] = None,             # ← prix TP2 (optionnel)
    price_tick: Optional[float] = None,      # tick (optionnel, sinon meta)
    notifier: Optional[Callable[[str], None]] = None,  # callback (optionnel)
):
    """
    Surveille la position :
      - Cas >=2 lots : détecte TP1 par réduction de taille (~50% exécutés), déplace SL -> BE et s'assure de TP2.
      - Cas 1 lot    : pas de split possible → on déplace SL -> BE quand le prix atteint TP1. (Pas de TP2 ici)
    IMPORTANT : on ne ferme rien au marché ici (les TP sont des LIMIT reduce-only).
    """
    # --- Normalisation côté ---
    side = (str(side) if side is not None else "").lower()
    if side not in ("buy", "sell"):
        try:
            pos_side = ((get_open_position(symbol) or {}).get("side") or "").lower()
            if pos_side in ("buy", "sell"):
                side = pos_side
        except Exception:
            pass
    if side not in ("buy", "sell"):
        LOGGER.warning("[BE] %s -> side invalide '%s', fallback 'buy'", symbol, side)
        side = "buy"

    # --- Tick / tolérance (utile pour logs et détection TP2) ---
    tick_from_arg = float(price_tick) if (price_tick is not None) else 0.0
    try:
        meta = get_contract_info(symbol) or {}
        tick_from_meta = float(meta.get("tickSize", 0.0) or 0.0)
    except Exception:
        tick_from_meta = 0.0

    ref = max(abs(entry), abs(tp1), abs(tp2 or 0.0), 1e-9)
    if tick_from_arg <= 0 or tick_from_arg > 0.01 * ref:
        tick = tick_from_meta if tick_from_meta > 0 else 0.0
    else:
        tick = tick_from_arg if tick_from_arg > 0 else (tick_from_meta if tick_from_meta > 0 else 0.0)

    gap = abs(tp1 - entry)
    raw_tol = (tick * 2.0) if tick > 0 else 0.0
    cap = gap * 0.25
    tol = min(raw_tol, cap) if cap > 0 else raw_tol

    _notify_wrap(notifier, f"[BE] Monitoring {symbol} | side {side} | entry {entry:.10f} | TP1 {tp1:.10f} | TP2 {tp2 if tp2 is not None else '-'} | tick {tick:.10f} | tol {tol:.10f}")

    # --- Boucle : suit la TAILLE de la position pour détecter TP1 rempli (si possible) ---
    initial_lots: Optional[int] = None
    target_lots_after_tp1: Optional[int] = None   # taille attendue après ~50% d'exécution
    seen_reduction = False                        # on n'agit qu'après avoir vu une baisse réelle

    while True:
        try:
            pos = get_open_position(symbol) or {}
            cur_lots = int(float(pos.get("currentQty", 0) or 0))

            if initial_lots is None:
                initial_lots = max(0, cur_lots)
                if initial_lots >= 2:
                    # Après TP1 (≈50%), il doit rester ceil(init/2)
                    target_lots_after_tp1 = (initial_lots + 1) // 2  # ceil
                    _notify_wrap(notifier, f"[BE] {symbol} initLots={initial_lots} → targetAfterTP1={target_lots_after_tp1}")
                else:
                    _notify_wrap(notifier, f"[BE] {symbol} initLots={initial_lots} (no split possible) → fallback: price-based BE at TP1")

            if cur_lots <= 0:
                LOGGER.info("[BE] %s -> position fermée ou inexistante", symbol)
                break

            # -------------- CAS 1 LOT : fallback sur PRIX --------------
            if initial_lots == 1:
                # progression obligatoire
                # (pas de BE si le prix n'a pas au moins rejoint l'entrée)
                try:
                    mark = pos.get("markPrice")
                    if mark is None:
                        mark = get_mark_price(symbol)
                    mark_price = float(mark)
                except Exception:
                    time.sleep(1.2)
                    continue

                progress_ok = (mark_price >= entry) if side == "buy" else (mark_price <= entry)
                if side == "buy":
                    tp_hit = progress_ok and (mark_price >= (tp1 - tol))
                else:
                    tp_hit = progress_ok and (mark_price <= (tp1 + tol))

                if tp_hit:
                    # Déplacer SL -> BE pour la taille entière (1 lot)
                    try:
                        modify_stop_order(
                            symbol=symbol, side=side,
                            existing_order_id=None,
                            new_stop=float(entry),
                            size_lots=cur_lots,
                        )
                        _notify_wrap(notifier, f"[BE] {symbol} -> SL déplacé à Break Even ({float(entry):.10f}) sur {cur_lots} lot")
                    except Exception as e:
                        LOGGER.exception("[BE] %s modify_stop_order (1-lot) a échoué: %s", symbol, e)
                    break

                time.sleep(1.2)
                continue
            # -----------------------------------------------------------

            # -------------- CAS >= 2 LOTS : détection par réduction --------------
            # On attend d'abord d'observer une BAISSE réelle de la taille
            if not seen_reduction:
                if cur_lots < initial_lots:
                    seen_reduction = True
                else:
                    # Pas encore de réduction, on ne fait rien
                    time.sleep(1.2)
                    continue

            # Maintenant on peut comparer à la cible "après TP1"
            if target_lots_after_tp1 is not None and cur_lots <= target_lots_after_tp1:
                _notify_wrap(notifier, f"[BE] {symbol} -> TP1 détecté par réduction: lots {initial_lots} ➜ {cur_lots}")

                # 1) Déplacer le SL → BE pour la taille restante
                try:
                    modify_stop_order(
                        symbol=symbol,
                        side=side,
                        existing_order_id=None,      # annule/recrée côté trader si besoin
                        new_stop=float(entry),
                        size_lots=cur_lots,
                    )
                    _notify_wrap(notifier, f"[BE] {symbol} -> SL déplacé à Break Even ({float(entry):.10f}) sur {cur_lots} lots")
                except Exception as e:
                    LOGGER.exception("[BE] %s modify_stop_order a échoué: %s", symbol, e)

                # 2) S'assurer que TP2 est présent (si demandé)
                if tp2 is not None and cur_lots > 0:
                    try:
                        if not _has_open_tp_at_price(symbol, side, float(tp2), tick):
                            r = place_reduce_only_tp_limit(symbol, side, take_profit=float(tp2), size_lots=cur_lots)
                            if r.get("ok"):
                                _notify_wrap(notifier, f"[BE] {symbol} -> TP2 posé à {float(tp2):.10f} pour {cur_lots} lots")
                            else:
                                LOGGER.error("[BE] Pose TP2 a échoué %s -> %s", symbol, r)
                        else:
                            _notify_wrap(notifier, f"[BE] {symbol} -> TP2 déjà présent autour de {float(tp2):.10f}")
                    except Exception as e:
                        LOGGER.exception("[BE] %s place_reduce_only_tp_limit a échoué: %s", symbol, e)

                break  # monitor terminé après action BE

            time.sleep(1.2)
            # ----------------------------------------------------------------------
        except Exception as e:
            LOGGER.exception("[BE] erreur sur %s: %s", symbol, e)
            time.sleep(2.0)


def launch_breakeven_thread(
    symbol: str,
    side: str,                              # 2e arg ici aussi
    entry: float,
    tp1: float,
    tp2: Optional[float] = None,
    price_tick: Optional[float] = None,
    notifier: Optional[Callable[[str], None]] = None,
):
    """
    Lance un thread indépendant pour surveiller le TP1 :
    - n'utilise PAS de market close (c'est le TP LIMIT reduce-only qui fait la sortie partielle)
    - >=2 lots : déplace SL -> BE quand ~50% exécutés + (re)pose TP2 si absent
    - 1 lot : déplace SL -> BE quand le PRIX atteint TP1 (pas de TP2)
    """
    threading.Thread(
        target=monitor_breakeven,
        args=(symbol, side, entry, tp1, tp2, price_tick, notifier),
        daemon=True,
        name=f"BE_{symbol}",
    ).start()
