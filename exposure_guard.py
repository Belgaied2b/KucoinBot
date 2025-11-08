# exposure_guard.py — limites d'exposition corrélée (comparaison en MARGE)
from __future__ import annotations
from typing import Dict

# buckets simplistes par “méga-facteur” (adapte à ta taxonomie)
BUCKETS = {
    "BTC": {"BTCUSDTM", "WBTCUSDTM", "BTCDOMUSDTM"},
    "ETH": {"ETHUSDTM", "LDOUSDTM", "OPUSDTM", "ARBUSDTM"},
    "SOL": {"SOLUSDTM", "JTOUSDTM", "JUPUSDTM"},
    "AI":  {"FETUSDTM", "RNDRUSDTM", "TAOUSDTM"},
}

# Caps exprimés en x RISK_USDT (donc en MARGE)
MAX_BUCKET_NOTIONAL = {
    "BTC":   3.0,
    "ETH":   2.0,
    "SOL":   2.0,
    "AI":    1.5,
    "_OTHER": 1.5,
}

# Levier pour convertir notionnel -> marge
try:
    from settings import LEVERAGE as _LEVERAGE
except Exception:
    _LEVERAGE = 1.0

def _to_margin(notional: float) -> float:
    lev = float(_LEVERAGE) if _LEVERAGE and _LEVERAGE > 0 else 1.0
    return float(notional) / max(lev, 1e-9)

def _to_notional(margin: float) -> float:
    lev = float(_LEVERAGE) if _LEVERAGE and _LEVERAGE > 0 else 1.0
    return float(margin) * lev

def bucket_of(sym: str) -> str:
    for b, sset in BUCKETS.items():
        if sym in sset:
            return b
    return "_OTHER"

def exposure_ok(open_notional_by_symbol: Dict[str, float] | None,
                new_sym: str,
                new_notional: float,
                risk_usdt: float) -> tuple[bool, str]:
    """
    Compare l'expo du BUCKET en **MARGE** au CAP (aussi en marge).
    - open_notional_by_symbol/new_notional sont supposés en notionnel → conversion marge.
    - cap = MAX_BUCKET_NOTIONAL[bucket] * risk_usdt (marge).
    """
    b = bucket_of(new_sym)

    # marge courante du bucket (convertie depuis notionnels)
    cur_margin = 0.0
    if open_notional_by_symbol:
        for s, n in open_notional_by_symbol.items():
            if bucket_of(s) == b:
                try:
                    cur_margin += _to_margin(float(n))
                except Exception:
                    continue

    # marge de la nouvelle position
    new_margin = _to_margin(float(new_notional))

    # cap en marge
    cap_margin = float(MAX_BUCKET_NOTIONAL.get(b, MAX_BUCKET_NOTIONAL["_OTHER"])) * float(risk_usdt)

    lhs_margin = cur_margin + new_margin
    ok = lhs_margin <= cap_margin

    # message explicite (marge + notionnel)
    lhs_notional = _to_notional(lhs_margin)
    cap_notional = _to_notional(cap_margin)
    msg = (f"bucket {b} cap {cap_margin:.0f} USDT (marge) / {cap_notional:.0f} notionnel, "
           f"would be {lhs_margin:.0f} (marge) / {lhs_notional:.0f} notionnel")

    return ok, msg
