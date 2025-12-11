# =====================================================================
# analyze_signal.py — VERSION DIAGNOSTIC (2025)
# Ajout de logs pour comprendre où le signal est rejeté.
# =====================================================================

import logging
import pandas as pd
import math
from typing import Dict, Any, Optional

from structure_utils import (
    analyze_structure,
    htf_trend_ok,
    bos_quality_details,
    commitment_score,
)

from indicators import (
    rsi,
    macd,
    ema,
    institutional_momentum,
    compute_ote,
    volatility_regime,
)

from stops import protective_stop_long, protective_stop_short
from tp_clamp import compute_tp1, compute_tp2

from institutional_data import compute_full_institutional_analysis

LOGGER = logging.getLogger(__name__)


def compute_premium_discount(df, lookback=80):
    if len(df) < lookback:
        return False, False
    w = df.tail(lookback)
    high, low = w["high"].max(), w["low"].min()
    mid = (high + low) / 2
    last = df["close"].iloc[-1]
    return last < mid, last > mid


def _safe_rr(entry, sl, tp1, bias):
    entry, sl, tp1 = float(entry), float(sl), float(tp1)
    if bias == "LONG":
        risk = entry - sl
        reward = tp1 - entry
    else:
        risk = sl - entry
        reward = entry - tp1

    if risk <= 0 or reward <= 0:
        return None

    return reward / risk


def _compute_exits(df, entry, bias, tick):
    """
    Calcule SL institutionnel + TP1/TP2 desk lead:
      - SL via protective_stop_long/short (structure + liquidité + ATR)
      - TP1 via compute_tp1 (RR dynamique)
      - TP2 via compute_tp2 (runner)
    """
    if bias == "LONG":
        sl, meta = protective_stop_long(df, entry, tick, return_meta=True)
    else:
        sl, meta = protective_stop_short(df, entry, tick, return_meta=True)

    tp1, rr_used = compute_tp1(entry, sl, bias, df=df, tick=tick)
    tp2 = compute_tp2(entry, sl, bias, df=df, tick=tick, rr1=rr_used)

    return {
        "sl": sl,
        "tp1": tp1,
        "tp2": tp2,
        "rr_used": rr_used,
        "sl_meta": meta,
    }


# =====================================================================
# CLASS ANALYZER AVEC LOGGAGE COMPLET
# =====================================================================

class SignalAnalyzer:

    def __init__(self, api_key, api_secret, api_passphrase):
        # RR minimum pour validation institutionnelle
        self.rr_min_inst = 1.3

    async def analyze(self, symbol, df_h1, df_h4, macro=None):
        LOGGER.info(f"[EVAL] ▶ START {symbol}")

        entry = float(df_h1["close"].iloc[-1])

        # 1 — STRUCTURE
        struct = analyze_structure(df_h1)
        bias = struct.get("trend", "").upper()
        LOGGER.info(f"[EVAL_PRE] STRUCT={struct}")

        if bias not in ("LONG", "SHORT"):
            LOGGER.info("[EVAL_REJECT] No trend detected")
            return None

        if not (struct.get("bos") or struct.get("cos") or struct.get("choch")):
            LOGGER.info("[EVAL_REJECT] No BOS/COS/CHoCH")
            return None

        # 2 — H4 alignement
        if not htf_trend_ok(df_h4, bias):
            LOGGER.info("[EVAL_REJECT] H4 alignment failed")
            return None

        # 3 — BOS QUALITY
        bos_q = bos_quality_details(
            df=df_h1,
            oi_series=struct.get("oi_series"),
            vol_lookback=60,
            vol_pct=0.8,
            oi_min_trend=0.003,
            oi_min_squeeze=-0.005,
            df_liq=df_h1,
            price=entry,
            tick=0.1,  # TODO: remplacer par le vrai tick Bitget par symbole
        )
        LOGGER.info(f"[EVAL_PRE] BOS_QUALITY={bos_q}")

        if not bos_q.get("ok", True):
            LOGGER.info("[EVAL_REJECT] BOS quality rejected")
            return None

        # 4 — INSTITUTIONAL
        inst = await compute_full_institutional_analysis(symbol, bias)
        inst_score = inst.get("institutional_score", 0)
        LOGGER.info(f"[INST_RAW] score={inst_score} details={inst}")

        if inst_score < 2:
            LOGGER.info("[EVAL_REJECT] Institutional score < 2")
            return None

        # 5 — MOMENTUM
        mom = institutional_momentum(df_h1)
        LOGGER.info(f"[EVAL_PRE] MOMENTUM={mom}")

        if bias == "LONG" and mom not in ("BULLISH", "STRONG_BULLISH"):
            LOGGER.info("[EVAL_REJECT] Momentum not bullish for LONG")
            return None
        if bias == "SHORT" and mom not in ("BEARISH", "STRONG_BEARISH"):
            LOGGER.info("[EVAL_REJECT] Momentum not bearish for SHORT")
            return None

        # 6 — PREMIUM / DISCOUNT
        discount, premium = compute_premium_discount(df_h1)
        LOGGER.info(f"[EVAL_PRE] PREMIUM={premium} DISCOUNT={discount}")

        # 7 — RR / SL / TP
        exits = _compute_exits(df_h1, entry, bias, tick=0.1)
        rr = _safe_rr(entry, exits["sl"], exits["tp1"], bias)
        LOGGER.info(
            f"[EVAL_PRE] RR={rr} raw_rr={exits['rr_used']} "
            f"sl={exits['sl']} tp1={exits['tp1']} tp2={exits['tp2']}"
        )

        if rr is None or rr < self.rr_min_inst:
            LOGGER.info("[EVAL_REJECT] RR < minimum")
            return None

        # 8 — VALIDATION FINALE
        LOGGER.info(f"[EVAL] VALID {symbol} RR={rr}")

        return {
            "symbol": symbol,
            "valid": True,
            "side": "buy" if bias == "LONG" else "sell",
            "bias": bias,
            "entry": entry,
            "sl": exits["sl"],
            "tp1": exits["tp1"],
            "tp2": exits["tp2"],
            "rr": rr,
            "qty": 1,  # la taille finale est gérée côté trader / sizing

            "structure": struct,
            "bos_quality": bos_q,
            "institutional": inst,
            "momentum": mom,
            "premium": premium,
            "discount": discount,
            "sl_meta": exits.get("sl_meta"),
            "rr_raw": exits.get("rr_used"),
        }
