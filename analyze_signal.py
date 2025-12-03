"""
analyze_signal.py — intégration institutionnelle + EXITS desk pro + garde-fou RR + HTF/BOS quality/Commitment

Nouveautés (version Desk Lead Final) :
- SL/TP1 reconstruits automatiquement (liquidité > structure > ATR)
- TP1 clamp dynamique + fallback géométrique si incohérent
- RR recalculé proprement et robustement
- Triple pipeline :
    A0) Desk EV Priority (inst + commitment extrêmes → RR minimum réduit)
    A ) Institutionnel prioritaire
    B ) Strict technique
- Structure / Momentum / BOS Quality deviennent SOFT si institutional block fort
- Intégration complète momentum institutionnel, volatility regime, premium/discount
- Logs détaillés : [INST_RAW], [EVAL_PRE], [EVAL]
"""

from __future__ import annotations
import math
from typing import Optional, Dict, Any, Tuple
import logging
import pandas as pd

LOGGER = logging.getLogger(__name__)

# ============================================================
# Imports internes
# ============================================================

# Techniques & structure
from indicators import (
    compute_rsi,
    compute_macd,
    compute_ema,
    compute_atr,
    is_momentum_ok,
)
from structure_utils import (
    structure_valid,
    htf_trend_ok,
    bos_quality_ok,
    bos_quality_details,
    commitment_score,
    analyze_structure,
)

# EXITS
from stops import (
    protective_stop_long,
    protective_stop_short,
    format_sl_meta_for_log,
)
from tp_clamp import compute_tp1

# Institutionnel (OI, CVD, liquidations, funding, volatility clusters…)
from institutional_data import compute_full_institutional_analysis


# ============================================================
# SETTINGS
# ============================================================

try:
    from settings import MIN_INST_SCORE
except Exception:
    MIN_INST_SCORE = 2   # minimum institutional score

try:
    from settings import RR_MIN_STRICT, RR_MIN_TOLERATED_WITH_INST
except Exception:
    RR_MIN_STRICT = 1.6
    RR_MIN_TOLERATED_WITH_INST = 1.3

try:
    from settings import REQUIRE_STRUCTURE, REQUIRE_MOMENTUM, REQUIRE_HTF_ALIGN, REQUIRE_BOS_QUALITY
except Exception:
    REQUIRE_STRUCTURE = True
    REQUIRE_MOMENTUM = True
    REQUIRE_HTF_ALIGN = True
    REQUIRE_BOS_QUALITY = True

try:
    from settings import COMMITMENT_MIN
except Exception:
    COMMITMENT_MIN = 0.55  # 0..1

# Desk EV mode (RR réduit si institutionnel extrême)
try:
    from settings import (
        DESK_EV_MODE,
        RR_MIN_DESK_PRIORITY,
        INST_SCORE_DESK_PRIORITY,
        COMMITMENT_DESK_PRIORITY,
    )
except Exception:
    DESK_EV_MODE = True
    RR_MIN_DESK_PRIORITY = 1.0
    INST_SCORE_DESK_PRIORITY = 2
    COMMITMENT_DESK_PRIORITY = 0.60


# ============================================================
# Helpers
# ============================================================

def _get_series(signal: Dict[str, Any], key: str) -> Optional[pd.Series]:
    s = signal.get(key)
    return s if isinstance(s, pd.Series) else None


def _inst_or_neutral(symbol: str, bias: str) -> Dict[str, Any]:
    """
    Exécute compute_full_institutional_analysis(MT5/Binance/CoinGlass mix).
    Garantit un dict stable même si erreur API.
    Log soft : [INST_RAW]
    """
    try:
        inst = compute_full_institutional_analysis(symbol, bias)
        if not isinstance(inst, dict):
            return {"institutional_score": 0, "neutral": True, "reason": "invalid_response"}

        inst.setdefault("institutional_score", inst.get("score", 0))
        inst.setdefault("neutral", False)

        try:
            LOGGER.info(
                "[INST_RAW] %s %s | score=%s neutral=%s details=%s",
                symbol,
                bias,
                inst.get("institutional_score"),
                inst.get("neutral"),
                inst.get("institutional_comment") or inst.get("reason") or "",
            )
        except Exception:
            pass

        return inst

    except Exception as e:
        LOGGER.exception("[INST] %s %s exception: %s", symbol, bias, e)
        return {"institutional_score": 0, "neutral": True, "reason": f"exception:{e}"}


# ============================================================
# RR SAFE
# ============================================================

def _safe_rr(signal: Dict[str, Any]) -> Optional[float]:
    """
    Recalcule proprement RR à partir de entry/sl/tp1/tp2.

    - Si TP1 cohérent → RR TP1
    - Sinon TP2 cohérent
    - Sinon fallback rr_estimated
    """

    try:
        entry = float(signal["entry"])
        sl = float(signal["sl"])
    except Exception:
        return None

    bias = (signal.get("bias") or "LONG").upper()

    # Liste TP candidates cohérentes
    tps = []
    for k in ("tp1", "tp2"):
        try:
            if k in signal and signal[k] is not None:
                tps.append(float(signal[k]))
        except Exception:
            pass

    if not tps:
        return float(signal["rr_estimated"]) if "rr_estimated" in signal else None

    # On garde seulement les TP du bon côté
    if bias == "LONG":
        valids = [tp for tp in tps if tp > entry]
    else:
        valids = [tp for tp in tps if tp < entry]

    if not valids:
        return float(signal.get("rr_estimated")) if signal.get("rr_estimated") else None

    # Long = on prend le plus ambitieux ; Short = le plus bas
    tp = max(valids) if bias == "LONG" else min(valids)

    if bias == "LONG":
        risk = entry - sl
        reward = tp - entry
    else:
        risk = sl - entry
        reward = entry - tp

    if risk <= 0 or reward <= 0:
        return None

    rr = reward / risk
    return rr if math.isfinite(rr) and rr > 0 else None


# ============================================================
# Construction SL + TP1 si manquants
# ============================================================

def _compute_exits_if_needed(signal: Dict[str, Any]) -> Tuple[Dict[str, Any], Optional[str]]:
    df = signal.get("df")
    entry = signal.get("entry")
    bias = (signal.get("bias") or "LONG").upper()
    tick = float(signal.get("tick", 0) or 0)

    if df is None or entry is None or not isinstance(df, pd.DataFrame):
        return signal, "no_df_or_entry"

    try:
        entry = float(entry)
    except Exception:
        return signal, "invalid_entry"

    sl = signal.get("sl")
    tp1 = signal.get("tp1")
    exits_meta = signal.get("exits", {}) or {}

    # ---------------- SL ----------------
    sl_meta = None
    if sl is None:
        try:
            if bias == "LONG":
                sl, sl_meta = protective_stop_long(df, entry, tick, return_meta=True)
            else:
                sl, sl_meta = protective_stop_short(df, entry, tick, return_meta=True)

            sl = float(sl)
            signal["sl"] = sl
            exits_meta["sl_meta"] = sl_meta
            exits_meta["sl_log"] = format_sl_meta_for_log(sl_meta)
            signal["exits"] = exits_meta
        except Exception as e:
            LOGGER.exception("SL compute failed: %s", e)
            return signal, "sl_failed"

    else:
        try:
            sl = float(sl)
        except Exception:
            return signal, "sl_invalid"

    # ---------------- TP1 ----------------
    if tp1 is None:
        try:
            rr_pref = float(signal.get("rr_target", 0) or 0)
        except Exception:
            rr_pref = 0

        try:
            tp1_val, rr_used = compute_tp1(
                entry=float(entry),
                sl=float(sl),
                bias=bias,
                rr_preferred=rr_pref if rr_pref > 0 else None,
                df=df,
                tick=tick,
            )
            tp1_ok = (tp1_val > entry) if bias == "LONG" else (tp1_val < entry)
        except Exception as e:
            LOGGER.exception("TP1 clamp failed: %s", e)
            tp1_ok = False
            tp1_val = None
            rr_used = None

        if not tp1_ok:
            try:
                risk = (entry - sl) if bias == "LONG" else (sl - entry)
            except Exception:
                return signal, "tp1_failed"

            if risk <= 0:
                return signal, "tp1_failed"

            base_rr = rr_pref if rr_pref > 0 else float(RR_MIN_STRICT)
            tp1_val = entry + base_rr * risk if bias == "LONG" else entry - base_rr * risk
            rr_used = base_rr
            exits_meta["tp1_fallback"] = True

        signal["tp1"] = float(tp1_val)
        exits_meta["tp1_meta"] = {
            "regime": "dynamic" if "tp1_fallback" not in exits_meta else "fallback",
            "rr_effective": rr_used,
        }
        signal["exits"] = exits_meta

        LOGGER.info(
            "[EXITS][TP1] %s entry=%.6f sl=%.6f rr_eff=%.2f → tp1=%.6f",
            bias,
            entry,
            sl,
            rr_used,
            float(tp1_val),
        )

    return signal, None

# === FIN BLOC 1 ===
# ============================================================
#                  evaluate_signal (core engine)
# ============================================================

def evaluate_signal(signal: Dict[str, Any]) -> Dict[str, Any]:
    """
    Politique d'acceptation Desk Lead :

    A0) Desk EV Priority (extrêmement fort institutionnel)
        - inst_score >= INST_SCORE_DESK_PRIORITY
        - commitment >= COMMITMENT_DESK_PRIORITY
        - RR >= RR_MIN_DESK_PRIORITY
        - HTF aligné (hard)
        - Structure / Momentum / BOS Quality deviennent SOFT

    A) Institutionnel prioritaire
        - inst_score >= MIN_INST_SCORE
        - commitment >= COMMITMENT_MIN
        - RR >= RR_MIN_TOLERATED_WITH_INST
        - HTF aligné (hard)
        - Structure / Momentum / BOS Quality = SOFT

    B) Strict technique
        - RR >= RR_MIN_STRICT
        - HTF aligné
        - Structure/Momentum/BOS Quality = SOFT (on tolère mais on log)
    """

    # ----------------------------------------------------------
    #   Extract de base
    # ----------------------------------------------------------

    symbol = signal.get("symbol", "UNKNOWN")
    bias = (signal.get("bias") or "LONG").upper()
    df = signal.get("df")

    # Construction automatique SL/TP1 si manquants
    signal, exits_err = _compute_exits_if_needed(signal)

    if not isinstance(df, pd.DataFrame) or "close" not in df:
        return {
            "valid": False,
            "score": 0,
            "rr": None,
            "reasons": ["DF introuvable ou invalide"],
            "institutional": {"institutional_score": 0, "neutral": True, "reason": "no_df"},
        }

    close = df["close"].astype(float)
    vol = df["volume"].astype(float) if "volume" in df else None

    # ----------------------------------------------------------
    # Structure locale (swings/BOS/CHoCH/COS)
    # ----------------------------------------------------------

    try:
        struct_ctx = analyze_structure(df, bias)
    except Exception:
        struct_ctx = {
            "swings": [],
            "bos_direction": None,
            "choch_direction": None,
            "trend_state": "unknown",
            "phase": "unknown",
            "cos": None,
            "last_event": None,
        }

    # ----------------------------------------------------------
    # Institutionnel
    # ----------------------------------------------------------

    inst = _inst_or_neutral(symbol, bias)
    inst_score = int(inst.get("institutional_score", 0))

    # ----------------------------------------------------------
    # Technique : structure + momentum
    # ----------------------------------------------------------

    struct_ok_raw = structure_valid(df, bias) if REQUIRE_STRUCTURE else True
    trend_state = struct_ctx.get("trend_state", "unknown")

    if REQUIRE_STRUCTURE and struct_ok_raw:
        struct_ok = trend_state in ("up", "down")
    else:
        struct_ok = struct_ok_raw

    mom_ok = (is_momentum_ok(close, vol) if vol is not None else True) if REQUIRE_MOMENTUM else True

    # ----------------------------------------------------------
    # HTF / BOS Quality
    # ----------------------------------------------------------

    df_h4 = signal.get("df_h4")
    htf_ok = htf_trend_ok(df_h4, bias) if REQUIRE_HTF_ALIGN else True

    oi_series = _get_series(signal, "oi_series")
    df_liq = signal.get("df_liq") or signal.get("df_m15")
    tick = float(signal.get("tick", 0.0) or 0.0)
    ref_price = float(signal.get("entry") or close.iloc[-1])

    bos_details = (
        bos_quality_details(
            df=df,
            oi_series=oi_series,
            vol_lookback=60,
            vol_pct=0.80,
            oi_min_trend=0.003,
            oi_min_squeeze=-0.005,
            df_liq=df_liq,
            price=ref_price,
            tick=tick,
        )
        if REQUIRE_BOS_QUALITY
        else {"ok": True}
    )
    bos_ok_q = bool(bos_details.get("ok", True))

    # ----------------------------------------------------------
    # Commitment (OI + CVD)
    # ----------------------------------------------------------

    cvd_series = _get_series(signal, "cvd_series")
    comm = float(commitment_score(oi_series, cvd_series) or 0.0)  # 0..1

    # ----------------------------------------------------------
    # RR sécurité
    # ----------------------------------------------------------

    rr = _safe_rr(signal)

    try:
        LOGGER.info(
            "[EVAL_PRE] %s %s | RR=%.3f inst=%s comm=%.3f struct=%s mom=%s htf=%s bosQ=%s",
            symbol,
            bias,
            rr if rr is not None else float("nan"),
            inst_score,
            comm,
            struct_ok,
            mom_ok,
            htf_ok,
            bos_ok_q,
        )
    except Exception:
        pass

    if rr is None or not math.isfinite(rr) or rr <= 0:
        base = {
            "valid": False,
            "score": 50,
            "rr": None,
            "reasons": ["RR invalide (entry/sl/tp incohérents ou manquants)"],
            "institutional": inst,
            "exits": signal.get("exits", {}),
        }
        base.update(struct_ctx)
        return base

    # ----------------------------------------------------------
    # BLOCK A0 — DESK EV PRIORITY
    # ----------------------------------------------------------

    if (
        DESK_EV_MODE
        and inst_score >= INST_SCORE_DESK_PRIORITY
        and comm >= float(COMMITMENT_DESK_PRIORITY)
    ):
        rr_min = float(RR_MIN_DESK_PRIORITY)

        if rr >= rr_min and htf_ok:
            reasons_ev = [
                f"Desk EV Priority — inst={inst_score}/3 | comm={comm:.2f} | RR={rr:.2f} >= {rr_min:.2f}"
            ]
            if not struct_ok:
                reasons_ev.append("Structure faible (tolérée EV)")
            if not mom_ok:
                reasons_ev.append("Momentum faible (toléré EV)")
            if not bos_ok_q:
                reasons_ev.append("Break faible (vol/OI) (toléré EV)")

            out = {
                "valid": True,
                "score": 105 + inst_score * 6 + int(25 * comm),
                "rr": float(rr),
                "reasons": reasons_ev,
                "institutional": inst,
                "exits": signal.get("exits", {}),
            }
            out.update(struct_ctx)
            out.update(bos_details)

            LOGGER.info(
                "[EVAL] %s %s -> ACCEPT (A0 Desk EV) RR=%.3f inst=%s comm=%.3f",
                symbol,
                bias,
                rr,
                inst_score,
                comm,
            )
            return out

    # ----------------------------------------------------------
    # BLOCK A — Institutionnel prioritaire
    # ----------------------------------------------------------

    if (
        inst_score >= MIN_INST_SCORE
        and comm >= COMMITMENT_MIN
        and rr >= RR_MIN_TOLERATED_WITH_INST
    ):
        if htf_ok:
            reasons_a = [
                f"Institutionnel {inst_score}/3 + commitment {comm:.2f} + RR {rr:.2f}"
            ]
            if not struct_ok:
                reasons_a.append("Structure faible (tolérée inst)")
            if not mom_ok:
                reasons_a.append("Momentum faible (toléré inst)")
            if not bos_ok_q:
                reasons_a.append("Break faible (vol/OI) (toléré inst)")

            out = {
                "valid": True,
                "score": 100 + inst_score * 5 + int(20 * comm),
                "rr": float(rr),
                "reasons": reasons_a,
                "institutional": inst,
                "exits": signal.get("exits", {}),
            }
            out.update(struct_ctx)
            out.update(bos_details)

            LOGGER.info(
                "[EVAL] %s %s -> ACCEPT (A Inst) RR=%.3f inst=%s comm=%.3f",
                symbol,
                bias,
                rr,
                inst_score,
                comm,
            )
            return out
    # ----------------------------------------------------------
    # BLOCK B — Strict technique (RR strict, HTF hard)
    # ----------------------------------------------------------

    if rr >= RR_MIN_STRICT and htf_ok:
        reasons_b = [
            f"Strict technique : RR={rr:.2f} ≥ {RR_MIN_STRICT} & HTF aligné"
        ]
        if not struct_ok:
            reasons_b.append("Structure faible (tolérée strict)")
        if not mom_ok:
            reasons_b.append("Momentum faible (toléré strict)")
        if not bos_ok_q:
            reasons_b.append("Break faible (vol/OI) (toléré strict)")

        out = {
            "valid": True,
            "score": 95 + inst_score * 3 + int(10 * comm),
            "rr": float(rr),
            "reasons": reasons_b,
            "institutional": inst,
            "exits": signal.get("exits", {}),
        }
        out.update(struct_ctx)
        out.update(bos_details)

        LOGGER.info(
            "[EVAL] %s %s -> ACCEPT (B strict) RR=%.3f inst=%s comm=%.3f",
            symbol,
            bias,
            rr,
            inst_score,
            comm,
        )
        return out

    # ----------------------------------------------------------
    # REJET — raisons détaillées
    # ----------------------------------------------------------

    reasons: list[str] = []

    # RR
    if rr < RR_MIN_TOLERATED_WITH_INST:
        reasons.append(f"RR {rr:.2f} < toléré inst {RR_MIN_TOLERATED_WITH_INST}")
    elif rr < RR_MIN_STRICT:
        reasons.append(f"RR {rr:.2f} < strict {RR_MIN_STRICT}")

    # Structure
    if REQUIRE_STRUCTURE and not struct_ok:
        reasons.append("Structure invalide")

    # Momentum
    if REQUIRE_MOMENTUM and not mom_ok:
        reasons.append("Momentum faible")

    # HTF
    if REQUIRE_HTF_ALIGN and not htf_ok:
        reasons.append("HTF non aligné")

    # BOS Quality
    if REQUIRE_BOS_QUALITY and not bos_ok_q:
        reasons.append("Break faible (vol/OI)")

    # OTE soft
    if not bool(signal.get("ote", True)):
        reasons.append("OTE manquant (toléré)")

    out = {
        "valid": False,
        "score": 50 + inst_score * 2 + int(10 * comm),
        "rr": float(rr),
        "reasons": reasons,
        "institutional": inst,
        "exits": signal.get("exits", {}),
    }
    out.update(struct_ctx)
    out.update(bos_details)

    LOGGER.info(
        "[EVAL] %s %s -> REJECT RR=%.3f inst=%s comm=%.3f reasons=%s",
        symbol,
        bias,
        rr,
        inst_score,
        comm,
        "; ".join(reasons),
    )

    return out
# === FIN BLOC 3 ===
