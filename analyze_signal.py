# =====================================================================
# analyze_signal.py — Desk Lead Bitget v1.0 (Version corrigée 2025)
# Compatible scanner.py / indicators.py / institutional_data.py
# =====================================================================

import logging
import pandas as pd
import math

from typing import Dict, Any, Optional

# STRUCTURE
from structure_utils import (
    analyze_structure,
    htf_trend_ok,
    bos_quality_details,
    commitment_score,
)

# INDICATEURS (exactement ceux dans indicators.py)
from indicators import (
    rsi,
    macd,
    ema,
    institutional_momentum,
    compute_ote,
    volatility_regime,
)

# STOPS & TAKE PROFIT
from stops import protective_stop_long, protective_stop_short
from tp_clamp import compute_tp1

# INSTITUTIONAL DATA (déjà existant)
from institutional_data import compute_full_institutional_analysis

LOGGER = logging.getLogger(__name__)


# =====================================================================
# HELPERS
# =====================================================================

def compute_premium_discount(df: pd.DataFrame, lookback: int = 80):
    """
    ICT premium/discount simple :
        - close > mid → premium
        - close < mid → discount
    """
    if len(df) < lookback:
        return False, False

    window = df.tail(lookback)
    high = window["high"].max()
    low = window["low"].min()
    mid = (high + low) / 2

    last = df["close"].iloc[-1]

    discount = last < mid
    premium = last > mid

    return discount, premium


def _safe_rr(entry: float, sl: float, tp1: float, bias: str) -> Optional[float]:
    try:
        entry, sl, tp1 = float(entry), float(sl), float(tp1)
    except:
        return None

    if bias == "LONG":
        risk = entry - sl
        reward = tp1 - entry
    else:  # SHORT
        risk = sl - entry
        reward = entry - tp1

    if risk <= 0 or reward <= 0:
        return None

    rr = reward / risk
    return rr if math.isfinite(rr) and rr > 0 else None


def _compute_exits(df: pd.DataFrame, entry: float, bias: str, tick: float):
    """SL institutionnel + TP1 dynamique Desk Lead."""
    if bias == "LONG":
        sl, meta = protective_stop_long(df, entry, tick, return_meta=True)
    else:
        sl, meta = protective_stop_short(df, entry, tick, return_meta=True)

    tp1, rr_used = compute_tp1(
        entry=float(entry),
        sl=float(sl),
        bias=bias,
        df=df,
        tick=tick,
    )

    return {
        "sl": float(sl),
        "tp1": float(tp1),
        "rr_used": float(rr_used),
        "sl_meta": meta,
    }


# =====================================================================
# CLASS — SIGNAL ANALYZER
# =====================================================================

class SignalAnalyzer:

    def __init__(self, api_key: str, api_secret: str, api_passphrase: str):
        self.api_key = api_key
        self.api_secret = api_secret
        self.api_passphrase = api_passphrase

        # RR min institutionnel
        self.rr_min_inst = 1.3

    # ------------------------------------------------------------
    async def analyze(
        self,
        symbol: str,
        df_h1: pd.DataFrame,
        df_h4: pd.DataFrame,
        macro: Dict[str, Any] = None,   # <== IMPORTANT : pour scanner.py
    ) -> Optional[Dict[str, Any]]:

        # ------------------------------------------------------------
        # 0) Base checks
        # ------------------------------------------------------------
        if len(df_h1) < 80 or len(df_h4) < 60:
            return None

        entry = float(df_h1["close"].iloc[-1])

        # ------------------------------------------------------------
        # 1) STRUCTURE H1
        # ------------------------------------------------------------
        struct = analyze_structure(df_h1)
        bias = struct.get("trend", "").upper()

        if bias not in ("LONG", "SHORT"):
            return None

        # Au moins un signal structurel
        if not (struct.get("bos") or struct.get("cos") or struct.get("choch")):
            return None

        # ------------------------------------------------------------
        # 2) H4 ALIGNMENT
        # ------------------------------------------------------------
        if not htf_trend_ok(df_h4, bias):
            return None

        # ------------------------------------------------------------
        # 3) BOS QUALITY + COMMITMENT
        # ------------------------------------------------------------
        oi_series = struct.get("oi_series")
        cvd_series = struct.get("cvd_series")

        bos_q = bos_quality_details(
            df=df_h1,
            oi_series=oi_series,
            vol_lookback=60,
            vol_pct=0.8,
            oi_min_trend=0.003,
            oi_min_squeeze=-0.005,
            df_liq=df_h1,
            price=entry,
            tick=0.1,
        )

        if not bos_q.get("ok", True):
            return None

        comm = float(commitment_score(oi_series, cvd_series) or 0.0)

        # ------------------------------------------------------------
        # 4) INSTITUTIONAL SCORE (Binance)
        # ------------------------------------------------------------
        inst = await compute_full_institutional_analysis(symbol, bias)
        inst_score = int(inst.get("institutional_score", 0))

        if inst_score < 2:
            return None

        # ------------------------------------------------------------
        # 5) INSTITUTIONAL MOMENTUM
        # ------------------------------------------------------------
        mom = institutional_momentum(df_h1)

        if bias == "LONG" and mom not in ("BULLISH", "STRONG_BULLISH"):
            return None
        if bias == "SHORT" and mom not in ("BEARISH", "STRONG_BEARISH"):
            return None

        # ------------------------------------------------------------
        # 6) PREMIUM / DISCOUNT
        # ------------------------------------------------------------
        discount, premium = compute_premium_discount(df_h1, 80)

        # ------------------------------------------------------------
        # 7) EXITS
        # ------------------------------------------------------------
        exits = _compute_exits(df_h1, entry, bias, tick=0.1)
        sl = exits["sl"]
        tp1 = exits["tp1"]
        rr_used = exits["rr_used"]

        if rr_used < self.rr_min_inst:
            return None

        rr = _safe_rr(entry, sl, tp1, bias)
        if rr is None or rr < self.rr_min_inst:
            return None

        # ------------------------------------------------------------
        # 8) RETURN FINAL SIGNAL
        # ------------------------------------------------------------
        return {
            "valid": True,
            "symbol": symbol,
            "side": "BUY" if bias == "LONG" else "SELL",
            "bias": bias,
            "entry": entry,
            "sl": sl,
            "tp1": tp1,
            "tp2": None,
            "rr": rr,
            "qty": 1,  # tu mettras ton sizing réel

            # Debug & contexte
            "institutional_score": inst_score,
            "institutional": inst,
            "structure": struct,
            "bos_quality": bos_q,
            "commitment": comm,
            "momentum": mom,
            "premium": premium,
            "discount": discount,
            "exits": exits,
        }
