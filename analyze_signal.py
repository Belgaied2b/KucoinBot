print("🔥 analyze_signal.py LOADED (VERSION DEBUG)")
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
from tp_clamp import compute_tp1
from institutional_data import compute_full_institutional_analysis
from settings import TP2_R_TARGET  # TP2 cible (RR)

LOGGER = logging.getLogger(__name__)


# =====================================================================
# PREMIUM / DISCOUNT
# =====================================================================

def compute_premium_discount(df: pd.DataFrame, lookback: int = 80):
    if len(df) < lookback:
        return False, False
    w = df.tail(lookback)
    high, low = w["high"].max(), w["low"].min()
    mid = (high + low) / 2
    last = df["close"].iloc[-1]
    return last < mid, last > mid


# =====================================================================
# RR SAFE
# =====================================================================

def _safe_rr(entry: float, sl: float, tp: float, bias: str) -> Optional[float]:
    entry, sl, tp = float(entry), float(sl), float(tp)
    if bias == "LONG":
        risk = entry - sl
        reward = tp - entry
    else:
        risk = sl - entry
        reward = entry - tp

    if risk <= 0 or reward <= 0:
        return None

    return reward / risk


# =====================================================================
# Helper — round to tick
# =====================================================================

def _round_to_tick(price: float, tick: float) -> float:
    if tick <= 0:
        return float(price)
    return round(price / tick) * tick


# =====================================================================
# EXITS ENGINE (SL + TP1 + TP2)
# =====================================================================

def _compute_exits(df: pd.DataFrame, entry: float, bias: str, tick: float) -> Dict[str, Any]:
    """
    Retourne :
      - sl : stop loss institutionnel (structure + liquidité)
      - tp1 : TP1 dynamique (tp_clamp.compute_tp1)
      - tp2 : TP2 runner basé sur TP2_R_TARGET
      - rr_used : RR utilisé pour TP1 (par le moteur tp_clamp)
      - sl_meta : meta data sur le stop (swing, liq, atr, ...)
    """
    # SL institutionnel
    if bias == "LONG":
        sl, meta = protective_stop_long(df, entry, tick, return_meta=True)
    else:
        sl, meta = protective_stop_short(df, entry, tick, return_meta=True)

    # TP1 dynamique (utilise sa propre logique RR/ATR)
    tp1, rr_used = compute_tp1(entry, sl, bias, df=df, tick=tick)

    # TP2 runner : simple RR fixe plus loin que TP1
    risk = abs(entry - sl)
    if risk <= 0:
        tp2 = None
    else:
        target_rr = float(TP2_R_TARGET or 2.8)
        if bias == "LONG":
            tp2_raw = entry + risk * target_rr
        else:
            tp2_raw = entry - risk * target_rr

        tp2 = _round_to_tick(tp2_raw, tick)

        # Sanity check : TP2 doit être plus loin que TP1 dans le bon sens
        if tp2 is not None:
            if bias == "LONG" and tp2 <= tp1:
                tp2 = None
            if bias == "SHORT" and tp2 >= tp1:
                tp2 = None

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
        # RR mini basé sur TP1 pour accepter un trade
        self.rr_min_inst = 1.3

    async def analyze(self, symbol: str, df_h1: pd.DataFrame, df_h4: pd.DataFrame, macro: Optional[Dict[str, Any]] = None):
        LOGGER.info(f"[EVAL] ▶ START {symbol}")

        entry = float(df_h1["close"].iloc[-1])

        # 1 — STRUCTURE H1
        struct = analyze_structure(df_h1)
        bias = struct.get("trend", "").upper()
        LOGGER.info(f"[EVAL_PRE] STRUCT={struct}")

        if bias not in ("LONG", "SHORT"):
            LOGGER.info("[EVAL_REJECT] No trend detected")
            return None

        if not (struct.get("bos") or struct.get("cos") or struct.get("choch")):
            LOGGER.info("[EVAL_REJECT] No BOS/COS/CHoCH")
            return None

        # 2 — ALIGNEMENT H4
        if not htf_trend_ok(df_h4, bias):
            LOGGER.info("[EVAL_REJECT] H4 alignment failed")
            return None

        # 3 — BOS QUALITY (volume + OI + liquidity)
        bos_q = bos_quality_details(
            df=df_h1,
            oi_series=struct.get("oi_series"),
            vol_lookback=60,
            vol_pct=0.8,
            oi_min_trend=0.003,
            oi_min_squeeze=-0.005,
            df_liq=df_h1,
            price=entry,
            tick=0.1,   # TODO: plus tard → tick par symbole via metadata Bitget
        )
        LOGGER.info(f"[EVAL_PRE] BOS_QUALITY={bos_q}")

        if not bos_q.get("ok", True):
            LOGGER.info("[EVAL_REJECT] BOS quality rejected")
            return None

        # 4 — INSTITUTIONAL (OI / CVD / funding / liquidations)
        inst = await compute_full_institutional_analysis(symbol, bias)
        inst_score = inst.get("institutional_score", 0)
        LOGGER.info(f"[INST_RAW] score={inst_score} details={inst}")

        if inst_score < 2:
            LOGGER.info("[EVAL_REJECT] Institutional score < 2")
            return None

        # 5 — MOMENTUM INSTITUTIONNEL
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

        # 7 — RR / SL / TP (exits)
        # Pour l’instant on reste sur tick fixe 0.1 → on branchera plus tard le vrai tick par symbole
        exits = _compute_exits(df_h1, entry, bias, tick=0.1)

        rr = _safe_rr(entry, exits["sl"], exits["tp1"], bias)
        LOGGER.info(
            "[EVAL_PRE] RR=%s raw_rr_tp1=%s sl=%s tp1=%s tp2=%s",
            rr,
            exits["rr_used"],
            exits["sl"],
            exits["tp1"],
            exits["tp2"],
        )

        if rr is None or rr < self.rr_min_inst:
            LOGGER.info("[EVAL_REJECT] RR < minimum")
            return None

        # 8 — VALIDATION FINALE
        LOGGER.info(f"[EVAL] VALID {symbol} RR={rr}")

        return {
            "valid": True,
            "symbol": symbol,
            "side": "BUY" if bias == "LONG" else "SELL",
            "bias": bias,

            "entry": entry,
            "sl": exits["sl"],
            "tp1": exits["tp1"],
            "tp2": exits["tp2"],   # utilisé par scanner.py pour poser TP2 runner
            "rr": rr,

            # Taille : pour l’instant 1.0 = multiplicateur dans BitgetTrader
            "qty": 1.0,

            # Métriques pour logs / Telegram
            "institutional_score": inst_score,
            "structure": struct,
            "bos_quality": bos_q,
            "institutional": inst,
            "momentum": mom,
            "premium": premium,
            "discount": discount,
        }
