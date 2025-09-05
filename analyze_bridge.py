# -*- coding: utf-8 -*-
# analyze_bridge.py — adapte analyze_signal(entry_price, df, inst, macro) -> dict attendu

from __future__ import annotations
from typing import Dict, Any, Optional

try:
    import pandas as pd  # type: ignore
except Exception:
    pd = None

import analyze_signal as core

def analyze_signal(
    symbol: Optional[str] = None,
    df_h1: Optional["pd.DataFrame"] = None,
    df_h4: Optional["pd.DataFrame"] = None,
    macro: Optional[Dict[str, float]] = None,
    institutional: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    if pd is None:
        raise RuntimeError("pandas requis pour analyze_bridge")
    df = df_h1 if df_h1 is not None else df_h4
    if df is None or len(df) == 0:
        raise ValueError("analyze_bridge: df_h1/df_h4 manquant")

    entry_price = float(df["close"].iloc[-1])
    inst = institutional or {}
    macr = macro or {}

    try:
        dec = core.analyze_signal(entry_price, df, inst, macr)
    except TypeError:
        dec = core.analyze_signal(entry_price=entry_price, df=df, inst=inst, macro=macr)

    side_raw = getattr(dec, "side", "NONE")
    side = "long" if side_raw == "LONG" else ("short" if side_raw == "SHORT" else "none")
    rr   = float(getattr(dec, "rr", 0.0) or 0.0)
    entry= float(getattr(dec, "entry", entry_price) or entry_price)
    sl   = float(getattr(dec, "sl", 0.0) or 0.0)
    tp1  = float(getattr(dec, "tp1", 0.0) or 0.0)
    tp2  = float(getattr(dec, "tp2", 0.0) or 0.0)
    tol  = list(getattr(dec, "tolerated", []) or [])
    reason = str(getattr(dec, "reason", "") or "")
    score  = float(getattr(dec, "score", 0.0) or 0.0)

    return {
        "valid": side in ("long", "short"),
        "side": side,
        "rr": rr,
        "entry": entry,
        "sl": sl,
        "tp1": tp1,
        "tp2": tp2,
        "tolerated": tol,
        "comments": [reason] if reason else [],
        "inst_score": score,
        "inst_ok_count": None,
        "pre_shoot": False
    }
