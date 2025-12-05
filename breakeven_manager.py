# breakeven_manager.py — Version Desk Lead Pro Final
# Gère TP1 → BE + TP2 Runner, sans double-trigger, sans conflit trailing.

import time
import logging
import threading
import os
import requests
import errno
import inspect
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

# =====================================================================
# 🔧 PARAMÈTRES GÉNÉRAUX
# =====================================================================
FEE_BUFFER_TICKS = int(os.getenv("BE_FEE_BUFFER_TICKS", "1"))
BE_ONLY_FROM_EXITS = os.getenv("BE_ONLY_FROM_EXITS", "1")  # sécurité anti-loops

# Telegram optionnel
TELEGRAM_TOKEN = os.getenv("TG_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TG_CHAT_ID")


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


# =====================================================================
# ✔️ UTILS TICK / PRIX
# =====================================================================
def _round_to_tick(x: float, tick: float) -> float:
    if tick > 0:
        s = round(float(x) / tick)
        return round(s * tick, 12)
    return float(x)


def _approx_price_equal(a, b, tick, tol_ticks=0.5) -> bool:
    if tick > 0:
        return abs(a - b) <= (tick * tol_ticks)
    return abs(a - b) <= 1e-9


def _notify(n, msg):
    LOGGER.info(msg)
    if n:
        try:
            n(msg)
        except Exception:
            pass


def _caller_hint():
    try:
        st = inspect.stack()
        if len(st) >= 3:
            f = st[2]
            return f"{f.filename}:{f.lineno}"
    except Exception:
        pass
    return "?"


def _caller_allowed():
    if BE_ONLY_FROM_EXITS != "1":
        return True
    c = _caller_hint().replace("\\", "/")
    return "exits_manager.py" in c or "breakeven_manager.py" in c


# =====================================================================
# 🔍 GESTION POSITION
# =====================================================================
def _get_pos_snapshot(symbol: str) -> Tuple[int, Optional[str], float]:
    try:
        pos = get_open_position(symbol) or {}
        lots = int(float(pos.get("currentQty", 0)))
        pos_id = None
        for k in ("positionId", "id", "positionID"):
            if pos.get(k):
                pos_id = str(pos[k])
                break
        mp = float(pos.get("markPrice")) if pos.get("markPrice") else float(get_mark_price(symbol))
        return max(0, lots), pos_id, mp
    except Exception:
        return 0, None, 0.0


def _has_open_tp_at_price(symbol, side, price, tick) -> bool:
    opp = "sell" if side == "buy" else "buy"
    try:
        for o in list_open_orders(symbol) or []:
            if str(o.get("reduceOnly", "")).lower() != "true":
                continue
            o_side = (o.get("side") or "").lower()
            if o_side != opp:
                continue
            t = (o.get("type") or o.get("orderType") or "").lower()
            if "limit" not in t:
                continue
            p = float(o.get("price") or 0.0)
            if _approx_price_equal(p, price, tick):
                return True
    except Exception:
        pass
    return False


# =====================================================================
# 🧵 FILE LOCK (anti-doublon cross-process)
# =====================================================================
_LOCK_DIR = "/tmp/kucoin_be_locks"
os.makedirs(_LOCK_DIR, exist_ok=True)

_MONITOR_TTL = 30
_ACTIVE = {}
_ACTIVE_TS = {}
_LOCK = threading.Lock()


def _lock_path(key):
    safe = key.replace("/", "_").replace(" ", "_")
    return os.path.join(_LOCK_DIR, safe + ".lock")


def _try_file_lock(key):
    path = _lock_path(key)
    now = time.time()
    try:
        if os.path.exists(path):
            if now - os.path.getmtime(path) < _MONITOR_TTL:
                return False
        with open(path, "w") as f:
            f.write(str(now))
        return True
    except Exception:
        return False


def _refresh_lock(key):
    try:
        with open(_lock_path(key), "w") as f:
            f.write(str(time.time()))
    except Exception:
        pass


def _release_lock(key):
    try:
        os.remove(_lock_path(key))
    except Exception:
        pass


# =====================================================================
# 🧠 FONCTION PRINCIPALE BE
# =====================================================================
def monitor_breakeven(symbol, side, entry, tp1, tp2=None, price_tick=None, notifier=None):

    # 0) sécurité appelant
    if not _caller_allowed():
        LOGGER.warning("[BE] call blocked (not from exits_manager)")
        return

    # Position pour key
    _, posid, _ = _get_pos_snapshot(symbol)
    key = f"{symbol}#{posid or round(entry, 6)}"

    # File-lock
    if not _try_file_lock(key):
        LOGGER.info(f"[BE] skip: already running for {key}")
        return

    try:
        # Tick
        meta = get_contract_info(symbol) or {}
        tick = float(price_tick or meta.get("tickSize") or 0.01)

        entry_r = _round_to_tick(entry, tick)
        tp1_r   = _round_to_tick(tp1, tick)
        tp2_r   = _round_to_tick(tp2, tick) if tp2 else None

        side = side.lower()

        lots0, posid, _ = _get_pos_snapshot(symbol)
        if lots0 <= 0:
            return

        _notify(notifier, f"[BE] Start {symbol} side={side} entry={entry_r} TP1={tp1_r} TP2={tp2_r}")

        moved = False
        target_after_tp1 = (lots0 + 1) // 2 if lots0 >= 2 else None

        while True:
            _refresh_lock(key)
            lots, _, mark = _get_pos_snapshot(symbol)

            if lots <= 0:
                LOGGER.info(f"[BE] {symbol} closed.")
                break

            # 1 lot mode
            if lots0 == 1:
                if mark == 0:
                    time.sleep(1)
                    continue

                hit = (mark >= tp1_r) if side == "buy" else (mark <= tp1_r)
                if hit and not moved:
                    sign = +1 if side == "buy" else -1
                    new_sl = _round_to_tick(entry_r + sign * FEE_BUFFER_TICKS * tick, tick)
                    try:
                        modify_stop_order(symbol, side, existing_order_id=None, new_stop=new_sl, size_lots=lots)
                        _notify(notifier, f"[BE] {symbol} TP1 hit (1 lot) → SL BE {new_sl}")
                    except Exception as e:
                        LOGGER.error(f"[BE] modify_stop_order failed: {e}")
                    break

                time.sleep(1)
                continue

            # >=2 lots : détection TP1
            if target_after_tp1 and lots <= target_after_tp1:
                if not moved:
                    sign = +1 if side == "buy" else -1
                    new_sl = _round_to_tick(entry_r + sign * FEE_BUFFER_TICKS * tick, tick)
                    try:
                        modify_stop_order(symbol, side, existing_order_id=None, new_stop=new_sl, size_lots=lots)
                        _notify(notifier, f"[BE] {symbol} TP1 detected → SL BE {new_sl}")
                    except Exception as e:
                        LOGGER.error(f"[BE] modify_stop_order failed: {e}")
                    moved = True

                # TP2 si absent
                if tp2_r:
                    if not _has_open_tp_at_price(symbol, side, tp2_r, tick):
                        try:
                            r = place_reduce_only_tp_limit(symbol, side, take_profit=tp2_r, size_lots=lots)
                            if r.get("ok"):
                                _notify(notifier, f"[BE] {symbol} TP2 placed {tp2_r} for {lots} lots")
                            else:
                                LOGGER.error(f"[BE] TP2 place failed: {r}")
                        except Exception as e:
                            LOGGER.error(f"[BE] TP2 error: {e}")
                break

            time.sleep(1)

    finally:
        _release_lock(key)
