import time
import logging
import threading
import os
import requests
import errno
from typing import Optional, Callable, Dict, Tuple

from kucoin_utils import get_contract_info
from kucoin_trader import (
    modify_stop_order,
    get_open_position,
    get_mark_price,
    place_reduce_only_tp_limit,
    list_open_orders,
)

LOGGER = logging.getLogger(__name__)

# ======== PARAMS ========
FEE_BUFFER_TICKS = int(os.getenv("BE_FEE_BUFFER_TICKS", "1"))
TELEGRAM_TOKEN = os.getenv("TG_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TG_CHAT_ID")


# ======== UTIL ========
def telegram_notifier(msg: str):
    if not TELEGRAM_TOKEN or not TELEGRAM_CHAT_ID:
        return
    try:
        requests.post(
            f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage",
            data={"chat_id": TELEGRAM_CHAT_ID, "text": msg},
            timeout=5
        )
    except Exception:
        pass


def _round_to_tick(x: float, tick: float) -> float:
    if tick > 0:
        return round(round(float(x) / tick) * tick, 12)
    return float(x)


def _has_open_tp(symbol: str, side: str, price: float, tick: float) -> bool:
    opp = "sell" if side == "buy" else "buy"
    try:
        for o in list_open_orders(symbol) or []:
            if str(o.get("reduceOnly", "")).lower() == "true":
                if (o.get("side") or "").lower() == opp:
                    p = float(o.get("price") or 0)
                    if abs(p - price) <= tick * 0.5:
                        return True
    except:
        pass
    return False


def _get_pos(symbol: str) -> Tuple[int, Optional[str], float]:
    try:
        pos = get_open_position(symbol) or {}
        lots = int(float(pos.get("currentQty", 0)))
        pid = pos.get("positionId") or pos.get("id")
        mark = float(pos.get("markPrice") or get_mark_price(symbol))
        return lots, str(pid) if pid else None, mark
    except:
        return 0, None, 0.0


def _make_key(symbol: str, entry: float, pid: Optional[str]) -> str:
    if pid:
        return f"{symbol}#pos:{pid}"
    return f"{symbol}#e:{round(entry, 8)}"


# ======== FILE LOCK ========
_LOCK_DIR = "/tmp/kucoin_be"
os.makedirs(_LOCK_DIR, exist_ok=True)

def _lockpath(k: str):
    return os.path.join(_LOCK_DIR, k.replace("/", "_"))

def _acquire_filelock(k: str) -> bool:
    p = _lockpath(k)
    if os.path.exists(p):
        return False
    try:
        with open(p, "w") as f:
            f.write("1")
        return True
    except:
        return False

def _release_filelock(k: str):
    try:
        os.remove(_lockpath(k))
    except:
        pass


# ========== MONITOR ==========
def monitor_breakeven(symbol: str, side: str, entry: float, tp1: float, tp2: Optional[float], tick: float, notifier):
    lots_init, pid, _ = _get_pos(symbol)
    key = _make_key(symbol, entry, pid)

    LOGGER.info(f"[BE] Monitoring {symbol} entry={entry} tp1={tp1} tp2={tp2}")

    moved = False
    target_after_tp1 = None
    if lots_init >= 2:
        target_after_tp1 = (lots_init + 1) // 2

    while True:
        lots, pid_now, mark = _get_pos(symbol)

        if lots <= 0:
            LOGGER.info(f"[BE] {symbol} position closed → stop monitor")
            break

        # ----------- SINGLE LOT MODE ----------
        if lots_init == 1:
            hit = (mark >= tp1) if side == "buy" else (mark <= tp1)
            if hit and not moved:
                sign = +1 if side == "buy" else -1
                new_sl = _round_to_tick(entry + sign * FEE_BUFFER_TICKS * tick, tick)
                try:
                    modify_stop_order(symbol, side, None, float(new_sl), lots)
                except Exception as e:
                    LOGGER.error(f"[BE] Modify stop failed: {e}")
                moved = True
                if notifier:
                    notifier(f"[BE] {symbol} SL → BE at {new_sl}")
                break

            time.sleep(1.2)
            continue

        # ----------- MULTI LOT MODE ----------
        if target_after_tp1 and lots <= target_after_tp1:
            if not moved:
                sign = +1 if side == "buy" else -1
                new_sl = _round_to_tick(entry + sign * FEE_BUFFER_TICKS * tick, tick)
                try:
                    modify_stop_order(symbol, side, None, float(new_sl), lots)
                except Exception as e:
                    LOGGER.error(f"[BE] Modify stop failed: {e}")
                moved = True
                if notifier:
                    notifier(f"[BE] {symbol} SL → BE at {new_sl}")

            if tp2 and lots > 0:
                if not _has_open_tp(symbol, side, float(tp2), tick):
                    place_reduce_only_tp_limit(symbol, side, float(tp2), lots)
                    if notifier:
                        notifier(f"[BE] {symbol} TP2 placed {tp2}")

            break

        time.sleep(1.2)

    _release_filelock(key)


# ========== LAUNCHER ==========
def launch_breakeven_thread(symbol: str, side: str, entry: float, tp1: float, tp2: Optional[float], price_tick: float, notifier=None):
    lots, pid, _ = _get_pos(symbol)
    key = _make_key(symbol, entry, pid)

    if not _acquire_filelock(key):
        LOGGER.info(f"[BE] Monitor already active → skip ({key})")
        return

    t = threading.Thread(
        target=monitor_breakeven,
        args=(symbol, side, entry, tp1, tp2, price_tick, notifier),
        daemon=True,
    )
    t.start()
