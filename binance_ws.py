# -*- coding: utf-8 -*-
"""
binance_ws.py — WebSocket Binance Futures pour capter les liquidations en temps réel.
On maintient un cache rolling 5m par symbole pour un accès rapide.
"""

import json
import threading
import time
from collections import defaultdict, deque
from typing import Dict, Deque, Tuple

import websocket  # pip install websocket-client

# Cache des liquidations : { "BTCUSDT": deque[(timestamp, notionnel), ...] }
_liq_cache: Dict[str, Deque[Tuple[float, float]]] = defaultdict(lambda: deque(maxlen=1000))
_lock = threading.Lock()
_ws = None


def _on_message(ws, message):
    """Callback sur réception de liquidation."""
    try:
        data = json.loads(message)
        # Peut être un dict ou une liste (arr stream)
        events = data if isinstance(data, list) else [data]
        for ev in events:
            if "o" not in ev:
                continue
            o = ev["o"]  # order data
            sym = o.get("s")  # ex: BTCUSDT
            qty = float(o.get("q", 0.0) or 0.0)
            price = float(o.get("p", 0.0) or 0.0)
            notionnel = qty * price
            ts = float(o.get("T", time.time() * 1000)) / 1000.0
            with _lock:
                _liq_cache[sym].append((ts, notionnel))
    except Exception as e:
        print(f"[binance_ws] parse error: {e}")


def _on_error(ws, error):
    print(f"[binance_ws] error: {error}")


def _on_close(ws, close_status_code, close_msg):
    print(f"[binance_ws] closed: {close_status_code} {close_msg}")


def _ws_thread():
    global _ws
    url = "wss://fstream.binance.com/ws/!forceOrder@arr"
    while True:
        try:
            _ws = websocket.WebSocketApp(
                url,
                on_message=_on_message,
                on_error=_on_error,
                on_close=_on_close,
            )
            _ws.run_forever()
        except Exception as e:
            print(f"[binance_ws] WS exception: {e}")
        time.sleep(5)  # retry


def start_ws():
    """Lance le thread WS si pas déjà actif."""
    t = threading.Thread(target=_ws_thread, daemon=True)
    t.start()


def get_liquidations_notional_5m(symbol: str) -> float:
    """
    Retourne le notionnel total liquidé sur les 5 dernières minutes pour un symbole.
    """
    now = time.time()
    sym = symbol.replace("USDTM", "USDT").upper()
    with _lock:
        dq = _liq_cache.get(sym, deque())
        recent = [n for ts, n in dq if now - ts <= 300]  # 5 min = 300s
    return float(sum(recent))


def get_liquidations_count(symbol: str) -> int:
    """Retourne le nombre d’ordres liquidés sur 5m."""
    now = time.time()
    sym = symbol.replace("USDTM", "USDT").upper()
    with _lock:
        dq = _liq_cache.get(sym, deque())
        recent = [1 for ts, n in dq if now - ts <= 300]
    return len(recent)
