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
    entry: float,
    tp1: float,
    side: str,
    price_tick: Optional[float] = None,         # ← 5e arg optionnel (compat appel à 6 args)
    notifier: Optional[Callable[[str], None]] = None,  # ← 6e arg optionnel
):
    """
    Surveille la position :
      - quand le prix atteint TP1 → ferme 50 % et déplace SL à BE (entrée).
    Options:
      - price_tick: pour appliquer une tolérance en ticks (évite faux déclenchements)
      - notifier(msg): callback (Telegram/log) optionnel
    """
    side = (side or "").lower()
    tol = (price_tick or 0.0) * 2  # tolérance = 2 ticks si price_tick fourni

    def _notify(msg: str):
        if notifier:
            try:
                notifier(msg)
            except Exception:
                pass
        LOGGER.info(msg)

    _notify(f"[BE] Monitoring {symbol} | entry {entry:.6f} | TP1 {tp1:.6f} | tol {tol:.6f}")

    while True:
        try:
            # 1) Lire la position ; si fermée → arrêt
            pos = get_open_position(symbol)
            if not pos or float(pos.get("currentQty", 0) or 0) == 0:
                LOGGER.info("[BE] %s -> position fermée ou inexistante", symbol)
                break

            # 2) markPrice depuis la pos ou fallback API publique
            mark = pos.get("markPrice")
            if mark is None:
                try:
                    mark = get_mark_price(symbol)
                except Exception:
                    time.sleep(1.2)
                    continue
            mark_price = float(mark)

            # 3) Condition TP1 avec tolérance
            if side == "buy":
                tp_hit = mark_price >= (tp1 - tol)
            else:
                tp_hit = mark_price <= (tp1 + tol)

            if tp_hit:
                _notify(f"[BE] {symbol} -> TP1 atteint ({mark_price:.6f}), déclenche BE + close partiel")
                # 1️⃣ Ferme 50 % de la position
                try:
                    close_partial_position(symbol, side, 0.5)
                except Exception as e:
                    LOGGER.exception("[BE] %s close_partial_position a échoué: %s", symbol, e)

                # 2️⃣ Déplace le stop à break-even (prix d'entrée)
                try:
                    modify_stop_order(symbol, side, float(entry))
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
    entry: float,
    tp1: float,
    side: str,
    price_tick: Optional[float] = None,                 # ← accepter 5e arg
    notifier: Optional[Callable[[str], None]] = None,   # ← accepter 6e arg
):
    """Lance un thread indépendant pour surveiller le TP1 (compatible 4 à 6 args)."""
    threading.Thread(
        target=monitor_breakeven,
        args=(symbol, entry, tp1, side, price_tick, notifier),  # ← passe 6 args en toute sécurité
        daemon=True
    ).start()
