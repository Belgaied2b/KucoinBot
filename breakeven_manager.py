# breakeven_manager.py
import time, logging, threading
from typing import Optional, Callable

from kucoin_trader import (
    modify_stop_order,
    close_partial_position,
    get_open_position,
    get_mark_price,   # fallback markPrice public
)

LOGGER = logging.getLogger(__name__)

def monitor_breakeven(
    symbol: str,
    side: str,                               # ← 2e arg
    entry: float,
    tp1: float,
    price_tick: Optional[float] = None,      # 5e arg optionnel
    notifier: Optional[Callable[[str], None]] = None,  # 6e arg optionnel
):
    """
    Surveille la position :
      - quand le prix atteint TP1 → ferme 50 % (en LOTS) et déplace SL à BE (entrée).
    """
    side = (str(side) if side is not None else "").lower()
    tol = (price_tick or 0.0) * 2  # tolérance = 2 ticks si price_tick fourni

    def _notify(msg: str):
        if notifier:
            try:
                notifier(msg)
            except Exception:
                pass
        LOGGER.info(msg)

    _notify(f"[BE] Monitoring {symbol} | side {side} | entry {entry:.6f} | TP1 {tp1:.6f} | tol {tol:.6f}")

    while True:
        try:
            # 1) Lire la position ; si fermée → arrêt
            pos = get_open_position(symbol)
            cur_lots = int(float(pos.get("currentQty", 0) or 0)) if pos else 0
            if cur_lots <= 0:
                LOGGER.info("[BE] %s -> position fermée ou inexistante", symbol)
                break

            # 2) markPrice depuis la pos ou fallback API publique
            mark = pos.get("markPrice") if pos else None
            if mark is None:
                try:
                    mark = get_mark_price(symbol)
                except Exception:
                    time.sleep(1.2)
                    continue
            mark_price = float(mark)

            # 3) Condition TP1 avec tolérance
            tp_hit = mark_price >= (tp1 - tol) if side == "buy" else mark_price <= (tp1 + tol)

            if tp_hit:
                _notify(f"[BE] {symbol} -> TP1 atteint ({mark_price:.6f}), close 50% + SL → BE")

                # 1️⃣ Ferme 50 % en LOTS (au moins 1 lot)
                try:
                    half_lots = max(1, cur_lots // 2)
                    close_partial_position(symbol, side, half_lots)
                except Exception as e:
                    LOGGER.exception("[BE] %s close_partial_position a échoué: %s", symbol, e)

                # 2️⃣ Déplace le stop à break-even (prix d'entrée) en recréant le stop
                try:
                    # Relit la taille restante pour poser le nouveau SL
                    pos2 = get_open_position(symbol) or {}
                    rest_lots = int(float(pos2.get("currentQty", 0) or 0))
                    if rest_lots > 0:
                        modify_stop_order(
                            symbol=symbol,
                            side=side,
                            existing_order_id=None,      # on annule/recrée en interne
                            new_stop=float(entry),
                            size_lots=rest_lots,
                        )
                        _notify(f"[BE] {symbol} -> SL déplacé à Break Even ({float(entry):.6f})")
                except Exception as e:
                    LOGGER.exception("[BE] %s modify_stop_order a échoué: %s", symbol, e)

                break

            time.sleep(1.2)  # cadence douce et réactive
        except Exception as e:
            LOGGER.exception("[BE] erreur sur %s: %s", symbol, e)
            time.sleep(2.0)


def launch_breakeven_thread(
    symbol: str,
    side: str,                              # ← 2e arg ici aussi
    entry: float,
    tp1: float,
    price_tick: Optional[float] = None,
    notifier: Optional[Callable[[str], None]] = None,
):
    """Lance un thread indépendant pour surveiller le TP1 (compatible 4 à 6 args)."""
    threading.Thread(
        target=monitor_breakeven,
        args=(symbol, side, entry, tp1, price_tick, notifier),  # ordre aligné
        daemon=True
    ).start()
