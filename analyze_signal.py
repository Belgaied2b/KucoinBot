# =====================================================================
# analyze_signal.py — Desk Lead Bitget v2.0
# Analyse institutionnelle stricte (structure + intent + momentum)
# Async — Compatible scanner Bitget + trader Bitget + institutional_data
# =====================================================================

import math
import logging
import pandas as pd
from typing import Dict, Any, Optional

# Structure & technique
from structure_utils import (
    analyze_structure,
    htf_confirm,
    bos_quality_details,
    commitment_score,
)

# Indicators (version REMISE EN COHÉRENCE avec ton indicators.py)
from indicators import (
    rsi,
    macd,
    ema,
    institutional_momentum,
    compute_ote,
    volatility_regime,
)

# Stops / TP
from stops import protective_stop_long, protective_stop_short
from tp_clamp import compute_tp1

# Institutional engine (Binance OI/Funding/Liq)
from institutional_data import compute_full_institutional_analysis


LOGGER = logging.getLogger(__name__)


# =====================================================================
# Helpers
# =====================================================================

def _safe_rr(entry: float, sl: float, tp1: float, bias: str) -> Optional[float]:
    """Compute RR safely and robustly."""
    try:
        entry = float(entry)
        sl = float(sl)
        tp1 = float(tp1)
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
    return rr if math.isfinite(rr) and rr > 0 else None


def _compute_exits(df: pd.DataFrame, entry: float, bias: str, tick: float) -> Dict[str, Any]:
    """Institutional SL + TP1 dynamic clamp."""
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
# Class — Desk Lead Analyzer
# =====================================================================

class DeskLeadAnalyzer:

    def __init__(self, rr_min_strict: float = 1.6, rr_min_inst: float = 1.3):
        self.rr_min_strict = rr_min_strict
        self.rr_min_inst = rr_min_inst

    # ------------------------------------------------------------
    async def analyze(
        self,
        symbol: str,
        df_h1: pd.DataFrame,
        df_h4: pd.DataFrame,
        tick: float,
        contract: Dict[str, Any],
    ) -> Optional[Dict[str, Any]]:
        """
        Analyse Desk Lead :
            - Structure H1 complète
            - BOS quality + commitment OI/CVD
            - HTF alignment (H4)
            - Institutional analysis (OI, FUND, CVD, LIQ)
            - Institutional momentum (MACD/RSI/volume)
            - Premium/Discount + volatility regime
            - SL/TP institutionnels
        """

        # =====================================================================
        # 0) Base checks
        # =====================================================================

        if df_h1 is None or df_h4 is None or len(df_h1) < 80 or len(df_h4) < 60:
            return None

        entry = float(df_h1["close"].iloc[-1])

        # =====================================================================
        # 1) STRUCTURE H1
        # =====================================================================

        struct = analyze_structure(df_h1)
        bias = struct.get("trend", "").upper()

        if bias not in ("LONG", "SHORT"):
            return None

        # Au moins un signal structurel
        if not (struct.get("bos") or struct.get("cos") or struct.get("choch")):
            return None

        # =====================================================================
        # 2) H4 ALIGNMENT
        # =====================================================================

        if not htf_confirm(df_h4, bias):
            return None

        # =====================================================================
        # 3) BOS QUALITY + COMMITMENT
        # =====================================================================

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
            tick=tick,
        )

        if not bos_q.get("ok", True):
            return None

        comm = float(commitment_score(oi_series, cvd_series) or 0.0)

        # =====================================================================
        # 4) INSTITUTIONAL ANALYSIS (Binance)
        # =====================================================================

        inst = await compute_full_institutional_analysis(symbol, bias)
        inst_score = float(inst.get("institutional_score", 0))

        try:
            LOGGER.info(
                f"[INST_RAW] {symbol} {bias} score={inst_score} "
                f"pressure={inst.get('pressure')} details={inst.get('details')}"
            )
        except:
            pass

        if inst_score < 2:
            return None

        # =====================================================================
        # 5) INSTITUTIONAL MOMENTUM
        # =====================================================================

        mom = institutional_momentum(df_h1)

        if bias == "LONG" and mom not in ("BULLISH", "STRONG_BULLISH"):
            return None
        if bias == "SHORT" and mom not in ("BEARISH", "STRONG_BEARISH"):
            return None

        # =====================================================================
        # 6) PREMIUM / DISCOUNT + VOLATILITY
        # =====================================================================

        regime = volatility_regime(df_h1)

        # Sanction si expansion non soutenue par BOS validé
        if regime == "HIGH" and not struct.get("bos"):
            return None

        # =====================================================================
        # 7) EXITS (SL + TP1 institutionnels)
        # =====================================================================

        exits = _compute_exits(df_h1, entry, bias, tick)
        sl = exits["sl"]
        tp1 = exits["tp1"]
        rr_used = exits["rr_used"]

        # Validation RR
        if rr_used < self.rr_min_inst:
            return None

        rr = _safe_rr(entry, sl, tp1, bias)
        if rr is None or rr < self.rr_min_inst:
            return None

        # =====================================================================
        # 8) PACK RESULT — VALID SIGNAL
        # =====================================================================

        signal = {
            "valid": True,
            "symbol": symbol,
            "bias": bias,
            "entry": entry,
            "sl": sl,
            "tp1": tp1,
            "rr": rr,
            "rr_eff": rr_used,

            "structure": struct,
            "bos_quality": bos_q,
            "commitment": comm,

            "institutional": inst,
            "institutional_score": inst_score,

            "momentum_inst": mom,
            "volatility_regime": regime,

            "exits": exits,
        }

        LOGGER.info(
            f"[EVAL] {symbol} {bias} -> VALID "
            f"RR={rr:.2f} inst={inst_score:.1f} comm={comm:.2f} mom={mom}"
        )

        return signal
