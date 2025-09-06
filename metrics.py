
from __future__ import annotations
import csv, os, datetime
from typing import Dict, Any
def _path(name: str) -> str:
    d = os.getenv("METRICS_DIR", "./metrics"); os.makedirs(d, exist_ok=True)
    today = datetime.date.today().isoformat(); return os.path.join(d, f"{today}_{name}.csv")
def _append_csv(path: str, row: Dict[str, Any]) -> None:
    exists = os.path.exists(path)
    with open(path, "a", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=list(row.keys()))
        if not exists: w.writeheader()
        w.writerow(row)
def log_signal(symbol: str, side: str, score: float, rr_gross: float, rr_net: float, fill_mode: str, note: str = ""):
    row = {"ts": datetime.datetime.utcnow().isoformat(),"symbol": symbol,"side": side,
           "score": round(score,4),"rr_gross": round(rr_gross,4),"rr_net": round(rr_net,4),
           "fill_mode": fill_mode,"note": note}
    _append_csv(_path("signals"), row)
def log_order(symbol: str, side: str, entry: float, sl: float, tp1: float, tp2: float, value_usdt: float, mode: str, status: str):
    row = {"ts": datetime.datetime.utcnow().isoformat(),"symbol": symbol,"side": side,"entry": entry,"sl": sl,
           "tp1": tp1,"tp2": tp2,"value_usdt": value_usdt,"mode": mode,"status": status}
    _append_csv(_path("orders"), row)
