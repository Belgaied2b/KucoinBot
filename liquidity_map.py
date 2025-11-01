"""
liquidity_map.py — Heatmap de liquidations à la manière des desks
- Utilise Binance Futures comme proxy via mapping (institutional_live).
- Agrège les forced orders (allForceOrders) par bandes de prix pour repérer les grappes proches.
"""
import time
import logging
import numpy as np
import pandas as pd
from typing import Dict, Any, Optional

from institutional_live import _map_kucoin_to_binance_futures, _get

LOGGER = logging.getLogger(__name__)

def _bucket_prices(prices: pd.Series, bucket_size: float) -> pd.DataFrame:
    if prices.empty: 
        return pd.DataFrame(columns=["bucket","count"])
    buckets = (prices / bucket_size).round().astype(int) * bucket_size
    df = buckets.value_counts().sort_index().reset_index()
    df.columns = ["bucket","count"]
    return df

def fetch_liquidations_heatmap(kucoin_symbol: str, limit: int = 1000, bucket_pct: float = 0.002) -> Dict[str, Any]:
    """
    bucket_pct=0.2% du prix → bucket de densité.
    Retourne: {"binance_symbol", "df": DataFrame[bucket,count], "nearest_cluster": {...}}
    """
    b_sym = _map_kucoin_to_binance_futures(kucoin_symbol)
    if not b_sym:
        return {"binance_symbol": None, "df": pd.DataFrame(), "nearest_cluster": None}

    # Binance Futures forced orders (liquidations)
    data = _get("https://fapi.binance.com/fapi/v1/allForceOrders", params={"symbol": b_sym, "limit": min(limit, 1000)}, timeout=8, retries=2)
    rows = data if isinstance(data, list) else data.get("orders", [])
    if not rows:
        return {"binance_symbol": b_sym, "df": pd.DataFrame(), "nearest_cluster": None}

    prices = pd.Series([float(r.get("avgPrice") or r.get("price") or 0.0) for r in rows], dtype=float)
    prices = prices[prices > 0]
    if prices.empty:
        return {"binance_symbol": b_sym, "df": pd.DataFrame(), "nearest_cluster": None}

    last_price = float(prices.iloc[-1])
    bucket = max(last_price * bucket_pct, 0.01)
    df = _bucket_prices(prices, bucket)

    # cluster le plus proche du dernier prix
    df["dist"] = (df["bucket"] - last_price).abs()
    nearest = df.sort_values(["dist","count"], ascending=[True, False]).head(1)
    cluster = None
    if not nearest.empty:
        row = nearest.iloc[0]
        cluster = {"price": float(row["bucket"]), "count": int(row["count"]), "distance": float(row["dist"])}

    return {"binance_symbol": b_sym, "df": df[["bucket","count"]], "nearest_cluster": cluster}
