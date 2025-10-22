"""
portfolio.py — suivi d'état léger: positions ouvertes, ordres en vol, corrélation simple.
"""
import time
from collections import deque
import pandas as pd

_positions = {}      # symbol -> {"side":"long/short","size":lots,"avg":price}
_pending = set()     # clientOid en vol
_returns = {}        # symbol -> deque des dernières n variations pour corrélation

MAX_RET_SAMPLES = 120

def track_returns(symbol: str, df: pd.DataFrame):
    if df.shape[0] < 3: return
    ret = df["close"].pct_change().dropna().tail(1).iloc[0]
    dq = _returns.get(symbol)
    if dq is None:
        dq = deque(maxlen=MAX_RET_SAMPLES)
        _returns[symbol] = dq
    dq.append(float(ret))

def correlated(symbol_a: str, symbol_b: str, thresh: float = 0.8) -> bool:
    if symbol_a == symbol_b: return True
    a = _returns.get(symbol_a); b = _returns.get(symbol_b)
    if not a or not b or len(a) < 20 or len(b) < 20: return False
    import numpy as np
    v = float(np.corrcoef(list(a), list(b))[0,1])
    return abs(v) >= thresh

def has_open_position(symbol: str) -> bool:
    return symbol in _positions

def mark_pending(client_oid: str):
    _pending.add(client_oid)

def unmark_pending(client_oid: str):
    _pending.discard(client_oid)
