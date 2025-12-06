# breakeven_manager.py — Version Bitget institutionnelle
# SL → BE net + TP2 auto après TP1, anti-doublon, file-lock.
# Compatible avec exits_manager.py (Bitget version).

import time
import logging
import threading
import os
import requests
from typing import Optional, Callable, Dict, Tuple

from bitget_utils import get_contract_info
from bitget_trader import (
    modify_stop_order,
    get_open_position,
    get_mark_price,
    place_reduce_only_tp_limit,
    list_open_orders,
)

LOGGER = logging.getLogger(__name__)

# -------------------------------------------------------------------
# PARAMÈTRES BE
# -------------------------------------------------------------------
FEE_BUFFER_TICKS = int(os.getenv("BE_FEE_BUFFER_TICKS", "1"))

TELEGRAM_TOKEN = os.getenv("TG_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TG_CHAT_ID")


def telegram_notifier(msg: str):
    if not TELEGRAM_TOKEN or not TELEGRAM_CHAT_ID:
        return
    try:
        requests.post(
            f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage",
            data={"chat_id": TELEGRAM_CHAT_ID, "text": msg},
            timeout=4
        )
    except:
        pass


# -------------------------------------------------------------------
# Utils
# -------------------------------------------------------------------
def _round_to_tick(x: float, tick: float) -> float:
    if tick <= 0:
        return float(x)
    steps = round(x / tick)
    return round(steps * tick, 12)


def _approx_price_equal(a: float, b: float, tick: float) -> bool:
    return abs(a - b) <= tick * 0.5


def _pos_snapshot(symbol: str) -> Tuple[int, Optional[str], float]:
    """Retourne (lots, positionId, markPrice)."""
    try:
        pos = get_open_position(symbol) or {}
        lots = int(float(pos.get("total", 0) or 0))
        pos_id = pos.get("posId") or pos.get("positionId")
        try:
            mp = float(pos.get("markPrice") or get_mark_price(symbol))
        except:
            mp = 0.0
        return max(0, lots), pos_id, mp
    except:
        return 0, None, 0.0


def _notify(notifier, msg: str):
    if notifier:
        try:
            notifier(msg)
        except:
            pass
    LOGGER.info(msg)


# -------------------------------------------------------------------
# Anti-doublon monitors (thread + file-lock)
# -------------------------------------------------------------------
_ACTIVE_MONITORS: Dict[str, threading.Thread] = {}
_ACTIVE_TS: Dict[str, float] = {}
_ACTIVE_LOCK = threading.Lock()
MONITOR_TTL = 25.0

LOCK_DIR = "/tmp/bitget_be_locks"
os.makedirs(LOCK_DIR, exist_ok=True)


def _lock_path(key: str):
    safe = key.replace("/", "_").replace(" ", "_")
    return os.path.join(LOCK_DIR, f"{safe}.lock")


def _try_file_lock(key: str) -> bool:
    path = _lock_path(key)
    now = time.time()

    if os.path.exists(path):
        try:
            mtime = os.path.getmtime(path)
            if now - mtime < MONITOR_TTL:
                return False
        except:
            pass

    try:
        with open(path, "w") as f:
            f.write(str(now))
        return True
    except:
        return False


def _refresh_file_lock(key: str):
    try:
        with open(_lock_path(key), "w") as f:
            f.write(str(time.time()))
    except:
        pass


def _release_file_lock(key: str):
    try:
        os.remove(_lock_path(key))
    except:
        pass


# -------------------------------------------------------------------
# GET KEY
# -------------------------------------------------------------------
def _monitor_key(symbol: str, entry: float, pos_id: Optional[str]):
    if pos_id:
        return f"{symbol}#{pos_id}"
    return f"{symbol}#e:{round(entry, 8)}"


# -------------------------------------------------------------------
# 💡 MONITOR BE PRINCIPAL
# -------------------------------------------------------------------
def monitor_breakeven(
    symbol: str,
    side: str,
    entry: float,
    tp1: float,
    tp2: Optional[float] = None,
    price_tick: Optional[float] = None,
    notifier: Optional[Callable[[str], None]] = None,
):
    # Tick
    meta = get_contract_info(symbol) or {}
    tick = price_tick or float(meta.get("priceTick", 0.0) or 0.0)
    if tick <= 0:
        tick = 0.001

    entry_r = _round_to_tick(entry, tick)
    tp1_r = _round_to_tick(tp1, tick)
    tp2_r = _round_to_tick(tp2, tick) if tp2 else None

    side = side.lower()

    # Snapshot initial
    lots, pos_id, _ = _pos_snapshot(symbol)
    key = _monitor_key(symbol, entry_r, pos_id)

    _notify(notifier, f"[BE] Start {symbol} | key={key} | entry={entry_r} | tp1={tp1_r} | tp2={tp2_r}")

    initial_lots = None
    moved_to_be = False

    while True:
        _refresh_file_lock(key)
        lots, pos_id_now, mark_price = _pos_snapshot(symbol)

        if lots <= 0:
            _notify(notifier, f"[BE] {symbol} position closed -> stop monitor")
            break

        if initial_lots is None:
            initial_lots = lots
            if initial_lots >= 2:
                target_after_tp1 = (initial_lots + 1) // 2
                _notify(notifier, f"[BE] {symbol} lots={initial_lots} → targetAfterTP1={target_after_tp1}")
            else:
                target_after_tp1 = None
                _notify(notifier, f"[BE] {symbol} single-lot mode")

        # --------------------------------------------------
        # MODE 1 LOT → BE si prix touche TP1
        # --------------------------------------------------
        if initial_lots == 1:
            if side == "buy":
                hit = mark_price >= tp1_r or _approx_price_equal(mark_price, tp1_r, tick)
            else:
                hit = mark_price <= tp1_r or _approx_price_equal(mark_price, tp1_r, tick)

            if hit and not moved_to_be:
                try:
                    sign = 1 if side == "buy" else -1
                    new_sl = entry_r + sign * FEE_BUFFER_TICKS * tick
                    new_sl = _round_to_tick(new_sl, tick)
                    modify_stop_order(symbol, side, new_stop=new_sl, size_lots=lots)
                    moved_to_be = True
                    _notify(notifier, f"[BE] {symbol} TP1 reached → SL->BE {new_sl}")
                except Exception as e:
                    LOGGER.exception("[BE] modify_stop error %s", e)
                break

            time.sleep(1.1)
            continue

        # --------------------------------------------------
        # MODE MULTI-LOTS → TP1 détecté par réduction de taille
        # --------------------------------------------------
        if initial_lots >= 2:
            if lots <= target_after_tp1 and not moved_to_be:
                # Move SL -> BE
                try:
                    sign = 1 if side == "buy" else -1
                    new_sl = entry_r + sign * FEE_BUFFER_TICKS * tick
                    new_sl = _round_to_tick(new_sl, tick)
                    modify_stop_order(symbol, side, new_stop=new_sl, size_lots=lots)
                    moved_to_be = True
                    _notify(notifier, f"[BE] {symbol} TP1 detected (lots {initial_lots}->{lots}) → SL->BE {new_sl}")
                except Exception as e:
                    LOGGER.exception("[BE] BE move fail %s", e)

                # Pose TP2 runner si absent
                if tp2_r:
                    exists = False
                    try:
                        for o in list_open_orders(symbol) or []:
                            if "limit" in (o.get("orderType") or "").lower():
                                if _approx_price_equal(float(o.get("price")), tp2_r, tick):
                                    exists = True
                                    break
                    except:
                        pass

                    if not exists:
                        try:
                            r = place_reduce_only_tp_limit(symbol, side, take_profit=tp2_r, size_lots=lots)
                            if r.get("ok"):
                                _notify(notifier, f"[BE] {symbol} TP2 placed @ {tp2_r} for {lots} lots")
                        except Exception as e:
                            LOGGER.exception("[BE] TP2 fail %s", e)

                break

            time.sleep(1.1)
            continue

    # cleanup
    try:
        with _ACTIVE_LOCK:
            if key in _ACTIVE_MONITORS:
                _ACTIVE_MONITORS.pop(key, None)
            _ACTIVE_TS[key] = time.time()
    except:
        pass

    _release_file_lock(key)


# -------------------------------------------------------------------
# LAUNCH THREAD
# -------------------------------------------------------------------
def launch_breakeven_thread(
    symbol: str,
    side: str,
    entry: float,
    tp1: float,
    tp2: Optional[float],
    price_tick: Optional[float],
    notifier: Optional[Callable[[str], None]] = None,
):
    meta = get_contract_info(symbol) or {}
    tick = price_tick or float(meta.get("priceTick", 0.0) or 0.0)
    if tick <= 0:
        tick = 0.001

    entry_r = _round_to_tick(entry, tick)
    pos_lots, pos_id, _ = _pos_snapshot(symbol)
    key = _monitor_key(symbol, entry_r, pos_id)

    if not _try_file_lock(key):
        return

    with _ACTIVE_LOCK:
        th = _ACTIVE_MONITORS.get(key)
        if th and th.is_alive():
            _release_file_lock(key)
            return

        t = threading.Thread(
            target=monitor_breakeven,
            args=(symbol, side, entry, tp1, tp2, tick, notifier),
            daemon=True,
            name=f"BE_{symbol}"
        )
        _ACTIVE_MONITORS[key] = t
        t.start()

    _release_file_lock(key)
