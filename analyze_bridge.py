# -*- coding: utf-8 -*-
"""
analyze_bridge.py — adapte analyze_signal avec support multi-timeframe strict :
H1 = setup, H4/D1 = contexte, M15 = timing
"""
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
                df_h4: Optional["pd.DataFrame"],
                df_d1: Optional["pd.DataFrame"],
                df_m15: Optional["pd.DataFrame"]):
    """Charge H1/H4/D1/M15 si absents via kucoin_utils.fetch_klines."""
    if all([df_h1 is not None, df_h4 is not None, df_d1 is not None, df_m15 is not None]):
        return df_h1, df_h4, df_d1, df_m15
    if symbol is None:
        raise ValueError("analyze_bridge: symbol requis si TF manquent")
    try:
        from kucoin_utils import fetch_klines as kfetch
    except Exception as e:
        raise RuntimeError(f"analyze_bridge: fetch_klines indisponible ({e})")

    h1_lim = int(os.getenv("H1_LIMIT", "500"))
    h4_lim = int(os.getenv("H4_LIMIT", "400"))
    d1_lim = int(os.getenv("D1_LIMIT", "200"))
    m15_lim = int(os.getenv("M15_LIMIT", "200"))

    if df_h1 is None:  df_h1 = kfetch(symbol, interval="1h",  limit=h1_lim)
    if df_h4 is None:  df_h4 = kfetch(symbol, interval="4h",  limit=h4_lim)
    if df_d1 is None:  df_d1 = kfetch(symbol, interval="1d",  limit=d1_lim)
    if df_m15 is None: df_m15 = kfetch(symbol, interval="15m", limit=m15_lim)

    return df_h1, df_h4, df_d1, df_m15


def analyze_signal(
    symbol: Optional[str] = None,
    df_h1: Optional["pd.DataFrame"] = None,
    df_h4: Optional["pd.DataFrame"] = None,
    df_d1: Optional["pd.DataFrame"] = None,
    df_m15: Optional["pd.DataFrame"] = None,
    macro: Optional[Dict[str, float]] = None,
    institutional: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    if pd is None:
        raise RuntimeError("pandas requis pour analyze_bridge")

    # Assure les TF si non fournis
    df_h1, df_h4, df_d1, df_m15 = _ensure_dfs(symbol, df_h1, df_h4, df_d1, df_m15)
    if any(df is None or len(df) == 0 for df in [df_h1, df_h4, df_d1, df_m15]):
        raise ValueError("analyze_bridge: certains TF sont vides (fetch KO ?)")

    # Entry price depuis la TF la plus fine
    entry_price = float(df_h1["close"].iloc[-1])
    inst = institutional or {}
    macr = macro or {}

    # Appel à ton analyze_signal core (multi-timeframe strict)
    try:
        dec = core.analyze_signal(symbol=symbol,
                                  entry_price=entry_price,
                                  df_h1=df_h1, df_h4=df_h4,
                                  df_d1=df_d1, df_m15=df_m15,
                                  inst=inst, macro=macr)
    except TypeError:
        # fallback compat signature ancienne
        dec = core.analyze_signal(entry_price, df_h1, inst, macr)

    # Normalisation du résultat
    side_raw = getattr(dec, "side", "NONE")
    side = "long" if str(side_raw).upper() == "LONG" else ("short" if str(side_raw).upper() == "SHORT" else "none")
    rr    = float(getattr(dec, "rr", 0.0) or 0.0)
    entry = float(getattr(dec, "entry", entry_price) or entry_price)
    sl    = float(getattr(dec, "sl", 0.0) or 0.0)
    tp1   = float(getattr(dec, "tp1", 0.0) or 0.0)
    tp2   = float(getattr(dec, "tp2", 0.0) or 0.0)
    tol   = list(getattr(dec, "tolerated", []) or [])
    reason= str(getattr(dec, "reason", "") or "")
    score = float(getattr(dec, "score", 0.0) or 0.0)

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
        "inst_ok_count": getattr(dec, "inst_ok_count", None),
        "pre_shoot": getattr(dec, "pre_shoot", False),
        "diagnostics": getattr(dec, "diagnostics", {}),
    }
