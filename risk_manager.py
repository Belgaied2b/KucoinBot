"""
risk_manager.py — sizing volatilité, limites portefeuille, coupe-circuits.
"""
from dataclasses import dataclass
import time
import math
import pandas as pd

# Paramètres par défaut (peuvent venir de settings si tu préfères)
ACCOUNT_EQUITY_USDT = 10000.0         # à mettre à jour via API si dispo
RISK_PER_TRADE_BPS = 25               # 25 bps = 0.25% du portefeuille
MAX_GROSS_EXPOSURE = 2.0              # 200% notionnel max
MAX_SYMBOL_EXPOSURE = 0.25            # 25% du portefeuille par symbole
MAX_ORDERS_PER_SCAN = 5               # filet de sécu
MAX_DRAWDOWN_DAY = 0.05               # 5% jour => kill switch
COOLDOWN_SEC = 30 * 60                # 30 minutes par symbole

_state = {
    "cooldown": {},        # symbol -> last_order_ts
    "gross_exposure": 0.0, # notionnel en cours
    "symbol_expo": {},     # symbol -> notionnel
    "orders_this_scan": 0,
    "day_pnl": 0.0,        # à brancher si tu as le PnL
}

@dataclass
class TradeSizing:
    size_lots: int
    price_rounded: float
    stop_distance: float
    notional: float
    risk_usdt: float

def _atr(df: pd.DataFrame, period: int = 14) -> float:
    h, l, c = df["high"], df["low"], df["close"]
    tr = pd.concat([(h-l), (h-c.shift()).abs(), (l-c.shift()).abs()], axis=1).max(axis=1)
    atr = tr.rolling(period).mean().iloc[-1]
    return float(atr)

def can_trade_now(symbol: str) -> bool:
    now = time.time()
    last = _state["cooldown"].get(symbol, 0)
    return (now - last) >= COOLDOWN_SEC

def register_order(symbol: str, notional: float):
    now = time.time()
    _state["cooldown"][symbol] = now
    _state["orders_this_scan"] += 1
    _state["gross_exposure"] += notional
    _state["symbol_expo"][symbol] = _state["symbol_expo"].get(symbol, 0.0) + notional

def reset_scan_counters():
    _state["orders_this_scan"] = 0

def guardrails_ok(symbol: str, est_notional: float) -> tuple[bool, str]:
    if _state["day_pnl"] <= -ACCOUNT_EQUITY_USDT * MAX_DRAWDOWN_DAY:
        return False, "KillSwitch: daily drawdown limit reached"
    if _state["orders_this_scan"] >= MAX_ORDERS_PER_SCAN:
        return False, "Cap orders per scan reached"
    if (_state["gross_exposure"] + est_notional) > ACCOUNT_EQUITY_USDT * MAX_GROSS_EXPOSURE:
        return False, "Gross exposure limit"
    if (_state["symbol_expo"].get(symbol, 0.0) + est_notional) > ACCOUNT_EQUITY_USDT * MAX_SYMBOL_EXPOSURE:
        return False, "Symbol exposure limit"
    if not can_trade_now(symbol):
        return False, "Symbol cooldown"
    return True, "OK"

def compute_vol_sizing(df: pd.DataFrame, entry_price: float, sl_price: float,
                       lot_multiplier: float, lot_size_min: int,
                       tick_size: float) -> TradeSizing:
    """
    Calcule lots pour risquer ~RISK_PER_TRADE_BPS * equity si SL touché.
    """
    stop_dist = abs(entry_price - sl_price)
    if stop_dist <= 0:
        stop_dist = max(0.5 * _atr(df), entry_price * 0.002)  # fallback

    target_risk = ACCOUNT_EQUITY_USDT * (RISK_PER_TRADE_BPS / 10000.0)
    # PnL par lot si stop: stop_dist * multiplier
    pnl_per_lot = stop_dist * max(lot_multiplier, 1e-9)
    lots = max(lot_size_min, int(math.floor(target_risk / max(pnl_per_lot, 1e-6))))
    lots = max(lots, lot_size_min)

    # notionnel estimé
    notional = entry_price * lot_multiplier * lots

    # arrondi prix au tick
    steps = int(entry_price / max(tick_size, 1e-9))
    price_r = round(steps * tick_size, 8)

    return TradeSizing(size_lots=lots, price_rounded=price_r,
                       stop_distance=stop_dist, notional=notional,
                       risk_usdt=target_risk)
