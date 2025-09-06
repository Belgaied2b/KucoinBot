# -*- coding: utf-8 -*-
# analyze_bridge.py — adapte analyze_signal(entry_price, df, inst, macro) -> dict attendu
from __future__ import annotations
from typing import Dict, Any, Optional
import os

try:
    import pandas as pd  # type: ignore
except Exception:
    pd = None

import analyze_signal as core

def _ensure_dfs(symbol: Optional[str],
                df_h1: Optional["pd.DataFrame"],
                df_h4: Optional["pd.DataFrame"]):
    """Charge H1/H4 si absents, via kucoin_utils.fetch_klines (symbol peut être USDT ou USDTM)."""
    if df_h1 is not None or df_h4 is not None:
        return df_h1, df_h4
    if symbol is None:
        raise ValueError("analyze_bridge: symbol requis si df_h1/df_h4 manquent")
    try:
        from kucoin_utils import fetch_klines as kfetch
    except Exception as e:
        raise RuntimeError(f"analyze_bridge: fetch_klines indisponible ({e})")
    h1_lim = int(os.getenv("H1_LIMIT", "500"))
    h4_lim = int(os.getenv("H4_LIMIT", "400"))
    df_h1 = kfetch(symbol, interval="1h", limit=h1_lim)
    df_h4 = kfetch(symbol, interval="4h", limit=h4_lim)
    return df_h1, df_h4

def analyze_signal(
    symbol: Optional[str] = None,
    df_h1: Optional["pd.DataFrame"] = None,
    df_h4: Optional["pd.DataFrame"] = None,
    macro: Optional[Dict[str, float]] = None,
    institutional: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    if pd is None:
        raise RuntimeError("pandas requis pour analyze_bridge")

    # Assure H1/H4 si non fournis
    df_h1, df_h4 = _ensure_dfs(symbol, df_h1, df_h4)

    # Choix du df à passer à ton analyze_signal d’origine
    df = df_h1 if df_h1 is not None and len(df_h1) else df_h4
    if df is None or len(df) == 0:
        raise ValueError("analyze_bridge: df_h1/df_h4 vides (fetch KO ?)")

    entry_price = float(df["close"].iloc[-1])
    inst = institutional or {}
    macr = macro or {}

    # Compat signatures (positional ou keywords)
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
