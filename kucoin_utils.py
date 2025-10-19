# kucoin_utils.py
from kucoin_adapter import fetch_klines, get_symbol_meta

def fetch_klines_safe(symbol: str, interval: str, limit: int):
    df = fetch_klines(symbol, interval, limit)
    if df is None or getattr(df, "empty", False):
        raise RuntimeError(f"klines empty: {symbol} {interval}")
    return df
