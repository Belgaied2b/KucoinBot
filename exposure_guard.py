# exposure_guard.py — limites d'exposition corrélée
from __future__ import annotations
from typing import Dict

# buckets simplistes par “méga-facteur” (adapte à ta taxonomie)
BUCKETS = {
    "BTC": {"BTCUSDTM","WBTCUSDTM","BTCDOMUSDTM"},
    "ETH": {"ETHUSDTM","LDOUSDTM","OPUSDTM","ARBUSDTM"},
    "SOL": {"SOLUSDTM","JTOUSDTM","JUPUSDTM"},
    "AI" : {"FETUSDTM","RNDRUSDTM","TAOUSDTM"},
}

MAX_BUCKET_NOTIONAL = {
    "BTC": 3.0,   # en xRISK_USDT
    "ETH": 2.0,
    "SOL": 2.0,
    "AI" : 1.5,
    "_OTHER": 1.5,
}

def bucket_of(sym: str) -> str:
    for b, sset in BUCKETS.items():
        if sym in sset:
            return b
    return "_OTHER"

def exposure_ok(open_notional_by_symbol: Dict[str,float], new_sym: str, new_notional: float, risk_usdt: float) -> tuple[bool,str]:
    b = bucket_of(new_sym)
    cur = 0.0
    for s, n in open_notional_by_symbol.items():
        if bucket_of(s) == b:
            cur += float(n)
    cap = MAX_BUCKET_NOTIONAL.get(b, MAX_BUCKET_NOTIONAL["_OTHER"]) * float(risk_usdt)
    return (cur + new_notional <= cap, f"bucket {b} cap {cap:.0f} USDT, would be {cur+new_notional:.0f}")
