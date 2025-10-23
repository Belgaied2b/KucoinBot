# breakeven_manager.py
import time, logging, threading
from kucoin_trader import modify_stop_order, close_partial_position, get_open_position

LOGGER = logging.getLogger(__name__)

def monitor_breakeven(symbol: str, entry: float, tp1: float, side: str):
    """
    Surveille la position : 
      - quand le prix atteint TP1 → ferme 50 % et déplace SL à BE (entrée).
    """
    LOGGER.info("[BE] Monitoring %s | entry %.4f | TP1 %.4f", symbol, entry, tp1)
    while True:
        try:
            pos = get_open_position(symbol)
            if not pos or float(pos.get("currentQty", 0)) == 0:
                LOGGER.info("[BE] %s -> position fermée ou inexistante", symbol)
                break

            mark_price = float(pos.get("markPrice", 0))
            if (side == "buy" and mark_price >= tp1) or (side == "sell" and mark_price <= tp1):
                LOGGER.info("[BE] %s -> TP1 atteint (%.4f), déclenche BE + close partiel", symbol, mark_price)
                # 1️⃣ Ferme 50 % de la position
                close_partial_position(symbol, side, 0.5)
                # 2️⃣ Déplace le stop à break-even
                modify_stop_order(symbol, side, entry)
                LOGGER.info("[BE] %s -> SL déplacé à Break Even (%.4f)", symbol, entry)
                break

            time.sleep(10)
        except Exception as e:
            LOGGER.exception("[BE] erreur sur %s: %s", symbol, e)
            time.sleep(20)


def launch_breakeven_thread(symbol: str, entry: float, tp1: float, side: str):
    """ Lance un thread indépendant pour surveiller le TP1 """
    threading.Thread(
        target=monitor_breakeven,
        args=(symbol, entry, tp1, side),
        daemon=True
    ).start()
