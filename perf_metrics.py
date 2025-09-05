# -*- coding: utf-8 -*-
"""
perf_metrics.py — MFE/MAE tracking + export CSV
"""

from __future__ import annotations
import os, json, time, csv, logging
from typing import Dict, Any, Optional
from kucoin_utils import fetch_klines

PERF_PATH = os.environ.get("PERF_PATH", "performance.json")
H1_LIMIT = int(os.environ.get("H1_LIMIT", "500"))

def load_perf_store() -> Dict[str, Any]:
    if not os.path.exists(PERF_PATH): return {}
    try:
        with open(PERF_PATH, "r", encoding="utf-8") as f: return json.load(f)
    except Exception: return {}

def save_perf_store(store: Dict[str, Any]) -> None:
    try:
        with open(PERF_PATH, "w", encoding="utf-8") as f: json.dump(store, f, ensure_ascii=False, indent=2)
    except Exception as e:
        logging.error("perf_metrics: save KO: %s", e)

def register_signal_perf(key: str, symbol: str, side: str, entry: Optional[float]) -> None:
    if entry is None: return
    store = load_perf_store()
    if key in store: return
    store[key] = {"symbol": symbol, "side": side, "entry": float(entry), "ts": time.time(),
                  "mfe": 0.0, "mae": 0.0, "last_update": 0.0}
    save_perf_store(store)

def update_perf_for_symbol(symbol: str) -> None:
    store = load_perf_store()
    items = [(k, v) for k, v in store.items() if v.get("symbol") == symbol]
    if not items: return
    try:
        df = fetch_klines(symbol, interval="1h", limit=H1_LIMIT)
    except Exception as e:
        logging.warning("[%s] update_perf: fetch_klines KO: %s", symbol, e); return
    if df is None or getattr(df, "empty", False): return
    for k, v in items:
        entry = float(v.get("entry", 0))
        if entry <= 0: continue
        start_ts = float(v.get("ts", 0)) * 1000.0
        sub = df[df["time"] >= start_ts]
        if getattr(sub, "empty", False): continue
        if v.get("side", "long").lower() == "long":
            max_high = float(sub["high"].max()); min_low = float(sub["low"].min())
            mfe = max(0.0, (max_high - entry) / entry); mae = max(0.0, (entry - min_low) / entry)
        else:
            min_low = float(sub["low"].min()); max_high = float(sub["high"].max())
            mfe = max(0.0, (entry - min_low) / entry); mae = max(0.0, (max_high - entry) / entry)
        v["mfe"] = max(float(v.get("mfe", 0.0)), float(mfe))
        v["mae"] = max(float(v.get("mae", 0.0)), float(mae))
        v["last_update"] = time.time()
        store[k] = v
    save_perf_store(store)

def export_csv(path: str = "performance_export.csv") -> str:
    store = load_perf_store()
    rows = [["key", "symbol", "side", "entry", "ts", "mfe", "mae"]]
    for k, v in store.items():
        rows.append([k, v.get("symbol"), v.get("side"), v.get("entry"),
                     v.get("ts"), v.get("mfe"), v.get("mae")])
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f); w.writerows(rows)
    return path
