# =====================================================================
# analyze_signal.py — Desk Lead Bitget v1.1 (compat indicators.py)
# =====================================================================

import math
import logging
import pandas as pd
from typing import Dict, Any, Optional

# Structure
from structure_utils import (
    analyze_structure,
    htf_trend_ok,
    bos_quality_details,
    commitment_score,
)

# Indicators (NOUVEAUX NOMS)
from indicators import (
    rsi,
    macd,
    ema,
    institutional_momentum,        # existe dans ton fichier
    compute_ote,
    volatility_regime,
)

from stops import protective_stop_long, protective_stop_short
from tp_clamp import compute_tp1
from institutional_data import compute_full_institutional_analysis

LOGGER = logging.getLogger(__name__)


# =====================================================================
# HELPERS
# =====================================================================

def _safe_rr(entry: float, sl: float, tp1: float, bias: str) -> Optional[float]:
    try:
        entry, sl, tp1 = float(entry), float(sl), float(tp1)
    except:
        return None

    if bias == "LONG":
        risk = entry - sl
        reward = tp1 - entry
    else:
        risk = sl - entry
        reward = entry - tp1

    if risk <= 0 or reward <= 0:
        return None

    rr = reward / risk
    return rr if math.isfinite(rr) else None


def _compute_exits(df: pd.DataFrame, entry: float, bias: str, tick: float):
    if bias == "LONG":
        sl, meta = protective_stop_long(df, entry, tick, return_meta=True)
    else:
        sl, meta = protective_stop_short(df, entry, tick, return_meta=True)

    tp1, rr_used = compute_tp1(entry=entry, sl=sl, bias=bias, df=df, tick=tick)

    return {
        "sl": float(sl),
        "tp1": float(tp1),
        "rr_used": float(rr_used),
        "sl_meta": meta,
    }


# =====================================================================
# ANALYZER
# =====================================================================

class DeskLeadAnalyzer:

    def __init__(self, rr_min_strict=1.6, rr_min_inst=1.3):
        self.rr_min_strict = rr_min_strict
        self.rr_min_inst = rr_min_inst

    async def analyze(
        self,
        symbol: str,
        df_h1: pd.DataFrame,
        df_h4: pd.DataFrame,
        tick: float,
        contract: Dict[str, Any]
    ) -> Optional[Dict[str, Any]]:

        if df_h1 is None or df_h4 is None or len(df_h1) < 80 or len(df_h4) < 60:
            return None

        entry = float(df_h1["close"].iloc[-1])

        # ------------------------------------------------------------
        # STRUCTURE
        # ------------------------------------------------------------
        struct = analyze_structure(df_h1)
        bias = struct.get("trend", "").upper()

        if bias not in ("LONG", "SHORT"):
            return None

        if not (struct.get("bos") or struct.get("cos") or struct.get("choch")):
            return None

        # ------------------------------------------------------------
        # HTF TREND
        # ------------------------------------------------------------
        if not htf_trend_ok(df_h4, bias):
            return None

        # ------------------------------------------------------------
        # BOS QUALITY + COMMITMENT
        # ------------------------------------------------------------
        bos_q = bos_quality_details(
            df=df_h1,
            oi_series=struct.get("oi_series"),
            vol_lookback=60,
            vol_pct=0.8,
            oi_min_trend=0.003,
            oi_min_squeeze=-0.005,
            df_liq=df_h1,
            price=entry,
            tick=tick,
        )

        if not bos_q.get("ok", True):
            return None

        comm = float(commitment_score(struct.get("oi_series"), struct.get("cvd_series")) or 0)

        # ------------------------------------------------------------
        # INSTITUTIONAL DATA (Binance)
        # ------------------------------------------------------------
        inst = await compute_full_institutional_analysis(symbol, bias)
        inst_score = inst.get("institutional_score", 0)

        if inst_score < 2:
            return None

        # ------------------------------------------------------------
        # MOMENTUM — compatible TON indicators.py
        # ------------------------------------------------------------
        mom = institutional_momentum(df_h1)

        if bias == "LONG" and mom not in ("BULLISH",):
            return None
        if bias == "SHORT" and mom not in ("BEARISH",):
            return None

        # ------------------------------------------------------------
        # PREMIUM / DISCOUNT + VOLATILITY
        # ------------------------------------------------------------
        discount, premium = compute_ote(df_h1, bias).values()
        regime = volatility_regime(df_h1)

        if regime == "HIGH" and not struct.get("bos"):
            return None

        # ------------------------------------------------------------
        # EXITS (SL + TP1)
        # ------------------------------------------------------------
        exits = _compute_exits(df_h1, entry, bias, tick)
        sl, tp1, rr_used = exits["sl"], exits["tp1"], exits["rr_used"]

        if rr_used < self.rr_min_inst:
            return None

        rr = _safe_rr(entry, sl, tp1, bias)
        if rr is None or rr < self.rr_min_inst:
            return None

        # ------------------------------------------------------------
        # SIGNAL VALID
        # ------------------------------------------------------------
        return {
            "valid": True,
            "symbol": symbol,
            "bias": bias,
            "entry": entry,
            "sl": sl,
            "tp1": tp1,
            "rr": rr,

            "structure": struct,
            "bos_quality": bos_q,
            "commitment": comm,

            "institutional": inst,
            "institutional_score": inst_score,

            "momentum": mom,
            "premium": premium,
            "discount": discount,
            "volatility_regime": regime,

            "exits": exits,
        }
