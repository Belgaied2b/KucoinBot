# -*- coding: utf-8 -*-
"""
inst_autotune.py — Tuner autonome des seuils institutionnels
- Quantile adaptatif sur l'historique des scores (par symbole)
- Ajustement par régime de volatilité (ATR% H1)
- Durcissement/allègement des minima composantes et du nb de composantes requises
"""

from __future__ import annotations
import os, math, collections
from typing import Dict, Any, Deque, Tuple, Optional

import numpy as np
import pandas as pd

# Base quantile & garde-fou
INST_Q_BASE   = float(os.getenv("INST_Q_BASE", "0.70"))
INST_Q_MIN    = float(os.getenv("INST_Q_MIN",  "0.50"))
INST_Q_MAX    = float(os.getenv("INST_Q_MAX",  "0.90"))
REQ_SCORE_FLOOR = float(os.getenv("REQ_SCORE_FLOOR", "1.20"))

# Minima composantes (base)
OI_MIN_BASE    = float(os.getenv("OI_MIN_BASE",    "0.25"))
DELTA_MIN_BASE = float(os.getenv("DELTA_MIN_BASE", "0.30"))
FUND_MIN_BASE  = float(os.getenv("FUND_MIN_BASE",  "0.05"))
LIQ_MIN_BASE   = float(os.getenv("LIQ_MIN_BASE",   "0.20"))
COMPONENTS_MIN_BASE = int(os.getenv("COMPONENTS_MIN_BASE", "2"))

# Régime vol (ATR%)
ATR_LOW  = float(os.getenv("ATR_LOW",  "0.006"))  # 0.6%
ATR_HIGH = float(os.getenv("ATR_HIGH", "0.020"))  # 2.0%

# Historique max
HIST_SIZE = int(os.getenv("INST_HIST_SIZE", "400"))

# Book imbalance optionnel
USE_BOOK = bool(int(os.getenv("USE_BOOK_IMBAL", "0")))
BOOK_MIN_BASE = float(os.getenv("BOOK_MIN_BASE", "0.30"))

def _atr_pct_h1(df_h1: "pd.DataFrame", n: int = 14) -> float:
    if df_h1 is None or len(df_h1) < n + 2:
        return 0.0
    df = df_h1.copy()
    prev_close = df["close"].shift(1)
    tr = pd.concat([
        df["high"] - df["low"],
        (df["high"] - prev_close).abs(),
        (df["low"] - prev_close).abs()
    ], axis=1).max(axis=1)
    atr = tr.rolling(n).mean().iloc[-1]
    close = float(df["close"].iloc[-1])
    return float(atr / close) if close > 0 else 0.0

class InstAutoTune:
    def __init__(self):
        self.hist: Dict[str, Deque[float]] = collections.defaultdict(lambda: collections.deque(maxlen=HIST_SIZE))

    def update_and_get(self, symbol: str, df_h1: "pd.DataFrame", inst: Dict[str, Any]) -> Dict[str, Any]:
        # 1) Mémorise le score courant
        s = float(inst.get("score", 0.0) or 0.0)
        self.hist[symbol].append(s)

        # 2) Régime: ATR% H1
        atrp = _atr_pct_h1(df_h1)
        dq = 0.0
        min_scale = 1.0
        comp_min = COMPONENTS_MIN_BASE

        if atrp <= ATR_LOW:
            # Marché calme -> on durcit un peu
            dq += +0.10
            min_scale *= 1.15
            comp_min = max(COMPONENTS_MIN_BASE, COMPONENTS_MIN_BASE + 1)
        elif atrp >= ATR_HIGH:
            # Marché nerveux -> on allège un peu
            dq += -0.10
            min_scale *= 0.85
            comp_min = max(1, COMPONENTS_MIN_BASE - 1)

        # 3) Quantile adaptatif
        q = min(INST_Q_MAX, max(INST_Q_MIN, INST_Q_BASE + dq))
        hist = list(self.hist[symbol])
        if len(hist) >= 50:
            req_q = float(np.quantile(hist, q))
            req_score = max(REQ_SCORE_FLOOR, req_q)
        else:
            req_score = REQ_SCORE_FLOOR  # au début, plancher

        # 4) Minima composantes ajustés
        oi_min    = OI_MIN_BASE    * min_scale
        delta_min = DELTA_MIN_BASE * min_scale
        fund_min  = FUND_MIN_BASE  * min_scale
        liq_min   = LIQ_MIN_BASE   * min_scale
        book_min  = BOOK_MIN_BASE  * min_scale if USE_BOOK else None

        return {
            "req_score": req_score,
            "components_min": int(comp_min),
            "oi_min": oi_min,
            "delta_min": delta_min,
            "fund_min": fund_min,
            "liq_min": liq_min,
            "book_min": book_min,
            "use_book": USE_BOOK,
            "q_used": q,
            "atr_pct": atrp,
        }

def components_ok(inst: Dict[str, Any], thr: Dict[str, Any]) -> Tuple[int, Dict[str, Optional[bool]]]:
    oi_ok   = (inst.get("oi_score", 0.0)    >= thr["oi_min"])
    dlt_ok  = (inst.get("delta_score", 0.0) >= thr["delta_min"])
    fund_ok = (inst.get("funding_score", 0.0) >= thr["fund_min"])
    liq_ok  = (inst.get("liq_score", 0.0)   >= thr["liq_min"])
    if thr.get("use_book", False):
        bk  = inst.get("book_imbal_score", None)
        book_ok = (bk is not None and bk >= float(thr.get("book_min", 0.0)))
        cnt = sum([oi_ok, dlt_ok, fund_ok, liq_ok, bool(book_ok)])
    else:
        book_ok = None
        cnt = sum([oi_ok, dlt_ok, fund_ok, liq_ok])
    details = {"oi_ok": oi_ok, "delta_ok": dlt_ok, "fund_ok": fund_ok, "liq_ok": liq_ok, "book_ok": book_ok}
    return cnt, details
