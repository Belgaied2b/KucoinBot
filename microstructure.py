"""
microstructure.py — signaux microstructure (orderbook top N) via Binance Futures proxy.
- Imbalance = (AskVol - BidVol) / (AskVol + BidVol)
- Spread (ticks) et pression (delta best sizes)
"""
import logging
import numpy as np
from typing import Dict, Any

from institutional_live import _map_kucoin_to_binance_futures, _get

LOGGER = logging.getLogger(__name__)

def fetch_orderbook_snapshot(kucoin_symbol: str, depth: int = 50) -> Dict[str, Any]:
    b_sym = _map_kucoin_to_binance_futures(kucoin_symbol)
    if not b_sym:
        return {"binance_symbol": None, "ok": False}
    data = _get("https://fapi.binance.com/fapi/v1/depth", params={"symbol": b_sym, "limit": min(depth, 500)}, timeout=6, retries=2)
    bids = data.get("bids", []) if data else []
    asks = data.get("asks", []) if data else []
    if not bids or not asks:
        return {"binance_symbol": b_sym, "ok": False}
    # bonnes pratiques : convertir en float et tronquer à depth
    bids = bids[:depth]; asks = asks[:depth]
    bid_vol = float(sum(float(b[1]) for b in bids))
    ask_vol = float(sum(float(a[1]) for a in asks))
    imb = (ask_vol - bid_vol) / max(ask_vol + bid_vol, 1e-9)

    best_bid = float(bids[0][0]); best_ask = float(asks[0][0])
    spread = best_ask - best_bid
    pressure = float(asks[0][1]) - float(bids[0][1])  # >0: ask heavier, <0: bid heavier

    return {"binance_symbol": b_sym, "ok": True, "imbalance": imb, "spread": spread, "pressure": pressure,
            "best_bid": best_bid, "best_ask": best_ask}
