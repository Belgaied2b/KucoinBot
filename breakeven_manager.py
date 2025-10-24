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
            # KuCoin renvoie parfois 'id' ou 'orderId', 'type'/'orderType', etc.
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
      - Quand ~50% ont été exécutés (via TP1 LIMIT reduce-only) → déplace SL à BE pour le reste.
      - S'assure que TP2 est présent (sinon le pose).
    IMPORTANT : on ne ferme plus rien au marché ici.
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

    # --- Tick / tolérance info (utile pour logs et détection TP2) ---
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

    def _notify(msg: str):
        if notifier:
            try:
                notifier(msg)
            except Exception:
                pass
        LOGGER.info(msg)

    _notify(f"[BE] Monitoring {symbol} | side {side} | entry {entry:.10f} | TP1 {tp1:.10f} | TP2 {tp2 if tp2 is not None else '-'} | tick {tick:.10f} | tol {tol:.10f}")

    # --- Boucle : on suit la TAILLE de la position pour détecter TP1 rempli ---
    initial_lots: Optional[int] = None
    half_threshold: Optional[int] = None     # taille attendue après exécution de 50%

    while True:
        try:
            pos = get_open_position(symbol) or {}
            cur_lots = int(float(pos.get("currentQty", 0) or 0))

            if initial_lots is None:
                initial_lots = max(0, cur_lots)
                # après TP1 (50%), il doit rester ~ceil(init/2)
                half_threshold = (initial_lots + 1) // 2  # ceil
                _notify(f"[BE] {symbol} initLots={initial_lots} → halfThreshold={half_threshold}")

            if cur_lots <= 0:
                LOGGER.info("[BE] %s -> position fermée ou inexistante", symbol)
                break

            # Optionnel : infos de progression (utile pour debug)
            try:
                mark = pos.get("markPrice")
                if mark is None:
                    mark = get_mark_price(symbol)
                LOGGER.debug("[BE] %s mark=%.10f lots=%d", symbol, float(mark), cur_lots)
            except Exception:
                pass

            # --- Détection robuste : TP1 rempli quand la taille a baissé d'environ 50% ---
            if half_threshold is not None and cur_lots <= half_threshold:
                _notify(f"[BE] {symbol} -> TP1 détecté par réduction de taille: lots {initial_lots} ➜ {cur_lots}")

                # 1) Déplacer le SL → BE pour la taille restante
                try:
                    modify_stop_order(
                        symbol=symbol,
                        side=side,
                        existing_order_id=None,      # annule/recrée côté trader si besoin
                        new_stop=float(entry),
                        size_lots=cur_lots,
                    )
                    _notify(f"[BE] {symbol} -> SL déplacé à Break Even ({float(entry):.10f}) sur {cur_lots} lots")
                except Exception as e:
                    LOGGER.exception("[BE] %s modify_stop_order a échoué: %s", symbol, e)

                # 2) S'assurer que TP2 est présent (si demandé)
                if tp2 is not None and cur_lots > 0:
                    try:
                        if not _has_open_tp_at_price(symbol, side, float(tp2), tick):
                            r = place_reduce_only_tp_limit(symbol, side, take_profit=float(tp2), size_lots=cur_lots)
                            if r.get("ok"):
                                _notify(f"[BE] {symbol} -> TP2 posé à {float(tp2):.10f} pour {cur_lots} lots")
                            else:
                                LOGGER.error("[BE] Pose TP2 a échoué %s -> %s", symbol, r)
                        else:
                            _notify(f"[BE] {symbol} -> TP2 déjà présent autour de {float(tp2):.10f}")
                    except Exception as e:
                        LOGGER.exception("[BE] %s place_reduce_only_tp_limit a échoué: %s", symbol, e)

                break  # monitor terminé après TP1

            time.sleep(1.2)
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
    - n'utilise PAS de market close (c'est le TP1 LIMIT reduce-only qui fait la sortie partielle)
    - quand TP1 est rempli (~50% de la taille), déplace SL -> BE et s'assure de TP2
    """
    threading.Thread(
        target=monitor_breakeven,
        args=(symbol, side, entry, tp1, tp2, price_tick, notifier),
        daemon=True,
        name=f"BE_{symbol}",
    ).start()
