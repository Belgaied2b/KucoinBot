"""
analyze_signal.py — intégration institutionnelle + EXITS desk pro + garde-fou RR + HTF/BOS quality/Commitment

Nouveautés:
- Auto-calcul des EXITS si manquants:
    SL: stops.protective_stop_long/short (liquidité > structure > ATR) avec meta + log line compacte
    TP1: tp_clamp.compute_tp1 (clamp dynamique par régime de volatilité) avec meta
- RR recalculé à partir de (entry, sl, tp1/tp2) si rr_estimated invalide/manquant
- Fallback TP1 géométrique si clamp renvoie un TP incohérent (RR_MIN_STRICT * risk)
- Priorité institutionnelle (MIN_INST_SCORE) + tolérance RR si inst OK et commitment OK
- Contrôles supplémentaires: HTF alignment, BOS quality (vol+OI), commitment score (OI + CVD)
- Mode Desk EV Priority: permet d'accepter un RR plus faible si le bloc institutionnel est extrême,
  avec structure/momentum/BOS considérés comme SOFT (tolérés si faibles).
"""
from __future__ import annotations
import math
from typing import Optional, Dict, Any, Tuple
import logging

import pandas as pd

LOGGER = logging.getLogger(__name__)

# Techniques & structure
from indicators import compute_rsi, compute_macd, compute_ema, compute_atr, is_momentum_ok
from structure_utils import (
    structure_valid,
    htf_trend_ok,
    bos_quality_ok,
    bos_quality_details,
    commitment_score,
    analyze_structure,
)

# EXITS (SL + TP1 dynamique)
from stops import (
    protective_stop_long,
    protective_stop_short,
    format_sl_meta_for_log,
)
from tp_clamp import compute_tp1

# Institutionnel
from institutional_data import compute_full_institutional_analysis

# ----------------------------- Settings --------------------------------
try:
    from settings import MIN_INST_SCORE
except Exception:
    MIN_INST_SCORE = 2  # score institutionnel minimum

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
    COMMITMENT_MIN = 0.55  # seuil 0..1

# Mode Desk EV priority (RR réduit si flux insto ultra forts)
try:
    from settings import (
        DESK_EV_MODE,
        RR_MIN_DESK_PRIORITY,
        INST_SCORE_DESK_PRIORITY,
        COMMITMENT_DESK_PRIORITY,
    )
except Exception:
    # Activé par défaut, avec seuils prudents alignés avec le .env recommandé
    DESK_EV_MODE = True
    RR_MIN_DESK_PRIORITY = 1.0       # RR mini accepté en mode EV
    INST_SCORE_DESK_PRIORITY = 2     # au moins 2/3 insto
    COMMITMENT_DESK_PRIORITY = 0.60  # commitment élevé (0..1)

# ----------------------------- Helpers --------------------------------
def _get_series(signal: Dict[str, Any], key: str) -> Optional[pd.Series]:
    s = signal.get(key)
    return s if isinstance(s, pd.Series) else None


def _inst_or_neutral(symbol: str, bias: str) -> Dict[str, Any]:
    """
    Appelle compute_full_institutional_analysis en tolérant les erreurs et
    en fournissant un résultat neutre si nécessaire.
    """
    try:
        inst = compute_full_institutional_analysis(symbol, bias)
        if not isinstance(inst, dict):
            return {"institutional_score": 0, "neutral": True, "reason": "invalid_response"}
        # Normalise quelques clés possibles
        if "institutional_score" not in inst:
            inst["institutional_score"] = inst.get("score", 0) or 0
        if "neutral" not in inst:
            inst["neutral"] = False
        return inst
    except Exception as e:
        return {"institutional_score": 0, "neutral": True, "reason": f"exception:{e}"}


def _safe_rr(signal: Dict[str, Any]) -> Optional[float]:
    """
    Calcule un RR cohérent à partir de entry/SL/TP1/TP2.
    - Priorité aux TP cohérents (du bon côté de l'entrée), TP1 puis TP2.
    - Si aucun TP valide: dernier recours sur rr_estimated.
    """
    # entry & SL doivent être présents
    try:
        entry = float(signal["entry"])
        sl = float(signal["sl"])
    except Exception:
        return None

    bias = (signal.get("bias") or "LONG").upper()

    # Candidats TP (tp1 / tp2)
    tp_candidates = []
    for key in ("tp1", "tp2"):
        if key in signal and signal[key] is not None:
            try:
                tp_candidates.append((key, float(signal[key])))
            except Exception:
                continue

    tp_price: Optional[float] = None
    if bias == "LONG":
        # TP doit être strictement au-dessus de l'entrée
        valids = [tp for _k, tp in tp_candidates if tp > entry]
        if valids:
            tp_price = max(valids)  # on prend le plus ambitieux
    else:
        # SHORT : TP doit être strictement en-dessous de l'entrée
        valids = [tp for _k, tp in tp_candidates if tp < entry]
        if valids:
            tp_price = min(valids)

    # Pas de TP cohérent -> fallback sur rr_estimated si présent
    if tp_price is None:
        rr = signal.get("rr_estimated")
        try:
            if rr is not None:
                rr = float(rr)
                if math.isfinite(rr) and rr > 0:
                    return rr
        except Exception:
            return None
        return None

    # Calcul RR propre
    if bias == "LONG":
        risk = entry - sl
        reward = tp_price - entry
    else:
        risk = sl - entry
        reward = entry - tp_price

    if risk <= 0 or reward <= 0:
        return None

    rr = reward / risk
    return float(rr) if math.isfinite(rr) and rr > 0 else None


def _compute_exits_if_needed(signal: Dict[str, Any]) -> Tuple[Dict[str, Any], Optional[str]]:
    """
    Si SL/TP1 manquants, tente de les construire à partir de df/entry/bias.
    Retourne (signal_enrichi, erreur éventuelle).
    - SL via protective_stop_long/short
    - TP1 via compute_tp1, avec fallback géométrique si besoin
    """
    df = signal.get("df")
    entry = signal.get("entry")
    bias = (signal.get("bias") or "LONG").upper()
    tick = float(signal.get("tick", 0.0) or 0.0)
    if df is None or entry is None or not isinstance(df, pd.DataFrame):
        return signal, "no_df_or_entry"

    try:
        entry = float(entry)
    except Exception:
        return signal, "invalid_entry"

    sl = signal.get("sl")
    tp1 = signal.get("tp1")
    exits_meta = signal.get("exits", {}) or {}

    # ---- SL ----
    sl_meta = None
    if sl is None:
        try:
            if bias == "LONG":
                sl, sl_meta = protective_stop_long(df, entry, tick, return_meta=True)
            else:
                sl, sl_meta = protective_stop_short(df, entry, tick, return_meta=True)
            sl = float(sl)
            exits_meta["sl_meta"] = sl_meta
            exits_meta["sl_log"] = format_sl_meta_for_log(sl_meta)
            signal["sl"] = sl
            signal["exits"] = exits_meta
        except Exception as e:
            LOGGER.exception("Failed to compute SL: %s", e)
            return signal, "sl_failed"
    else:
        try:
            sl = float(sl)
        except Exception:
            sl = None

    # ---- TP1 ----
    if tp1 is None and sl is not None:
        tp1_val = None
        tp1_meta = None

        # 1) tentative via compute_tp1 (clamp dynamique)
        try:
            rr_pref = float(signal.get("rr_target", 0.0) or 0.0)
            tp1_val, rr_used = compute_tp1(
                entry=float(entry),
                sl=float(sl),
                bias=bias,
                rr_preferred=rr_pref if rr_pref > 0 else None,
                df=df,
                tick=float(tick),
            )
            tp1_meta = {
                "regime": "dynamic",
                "rr_base": rr_pref if rr_pref > 0 else float(RR_MIN_STRICT),
                "rr_effective": float(rr_used),
            }
        except Exception as e:
            LOGGER.exception("Failed to compute TP1 via clamp, will try fallback: %s", e)
            tp1_val, tp1_meta = None, None

        # 2) validation du TP clampé
        tp1_ok = False
        if tp1_val is not None:
            try:
                tp1 = float(tp1_val)
                if bias == "LONG":
                    tp1_ok = tp1 > entry
                else:
                    tp1_ok = tp1 < entry
            except Exception:
                tp1 = None
                tp1_ok = False

        # 3) Fallback géométrique si clamp incohérent
        if not tp1_ok:
            try:
                if bias == "LONG":
                    risk = entry - float(sl)
                else:
                    risk = float(sl) - entry
            except Exception:
                return signal, "tp1_failed"

            if risk <= 0:
                return signal, "tp1_failed"

            base_rr = float(signal.get("rr_target", 0.0) or 0.0)
            if base_rr <= 0:
                base_rr = float(RR_MIN_STRICT)

            if bias == "LONG":
                tp1 = entry + base_rr * risk
            else:
                tp1 = entry - base_rr * risk

            tp1_meta = {
                "regime": "fallback",
                "rr_base": base_rr,
                "rr_effective": base_rr,
            }
            exits_meta["tp1_fallback"] = True

        # 4) enregistrement & log sécurisé
        exits_meta["tp1_meta"] = tp1_meta
        signal["tp1"] = float(tp1)
        signal["exits"] = exits_meta

        # Log TP1 sécurisé: tp1_meta peut être None ou non-dict
        if isinstance(tp1_meta, dict):
            rr_base_log = str(tp1_meta.get("rr_base"))
            rr_eff_log = float(tp1_meta.get("rr_effective", 0.0))
            regime_log = str(tp1_meta.get("regime"))
        else:
            rr_base_log = "n/a"
            rr_eff_log = 0.0
            regime_log = "n/a"

        LOGGER.info(
            "[EXITS][TP1] side=%s entry=%.12f sl=%.12f rr_base=%s rr_eff=%.4f regime=%s -> tp1=%.12f",
            "LONG" if bias == "LONG" else "SHORT",
            float(entry),
            float(sl),
            rr_base_log,
            rr_eff_log,
            regime_log,
            float(tp1),
        )

    return signal, None


# ----------------------------------------------------------------------
#                            evaluate_signal
# ----------------------------------------------------------------------
def evaluate_signal(signal: Dict[str, Any]) -> Dict[str, Any]:
    """
    Politique d'acceptation (desk pro) :

      A0) Desk EV priority :
          - DESK_EV_MODE activé
          - inst_score >= INST_SCORE_DESK_PRIORITY (ex: 2/3)
          - commitment_score >= COMMITMENT_DESK_PRIORITY (ex: 0.60)
          - RR >= RR_MIN_DESK_PRIORITY (ex: 1.0)
          - HTF aligné (hard)
          - BOS quality, Structure, Momentum = SOFT (tolérés si faibles, juste logués)

      A) Institutionnel prioritaire :
         - inst_score >= MIN_INST_SCORE
         - commitment_score >= COMMITMENT_MIN
         - RR >= RR_MIN_TOLERATED_WITH_INST
         - HTF aligné (hard)
         - Structure/Momentum/BOS = SOFT

      B) Strict technique :
         - RR >= RR_MIN_STRICT
         - Structure/Momentum/HTF/BOS = HARD (tous requis)
    """
    # ------------- Données de base -------------
    symbol = signal.get("symbol", "UNKNOWN")
    bias = (signal.get("bias") or "LONG").upper()
    df = signal.get("df")

    # construit SL/TP1 si besoin (SL liquidité/structure/ATR + TP1 clamp dynamique)
    signal, exits_err = _compute_exits_if_needed(signal)

    if not isinstance(df, pd.DataFrame) or "close" not in df:
        return {
            "valid": False,
            "score": 0,
            "rr": None,
            "reasons": ["DF introuvable ou invalide"],
            "institutional": {"institutional_score": 0, "neutral": True, "reason": "no_df"},
        }

    close = df["close"]
    vol = df["volume"] if "volume" in df else None

    # ------------- Structure locale (swings/BOS/CHoCH/COS) -------------
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

    # ------------- Institutionnel (tolérant) -------------
    inst = _inst_or_neutral(symbol, bias)
    inst_score = int(inst.get("institutional_score", 0))

    # ------------- Techniques -------------
    struct_ok_raw = structure_valid(df, bias) if REQUIRE_STRUCTURE else True
    trend_state = struct_ctx.get("trend_state", "unknown") if isinstance(struct_ctx, dict) else "unknown"
    if REQUIRE_STRUCTURE and struct_ok_raw:
        struct_ok = trend_state in ("up", "down")
    else:
        struct_ok = struct_ok_raw

    mom_ok = (is_momentum_ok(close, vol) if vol is not None else True) if REQUIRE_MOMENTUM else True

    # ------------- Garde-fous HTF/BOS -------------
    df_h4 = signal.get("df_h4")
    htf_ok = htf_trend_ok(df_h4, bias) if REQUIRE_HTF_ALIGN else True

    oi_series = _get_series(signal, "oi_series")
    df_liq = signal.get("df_liq") or signal.get("df_m15")
    tick = float(signal.get("tick", 0.0) or 0.0)
    ref_price = float(signal.get("entry") or close.iloc[-1])

    bos_details = bos_quality_details(
        df=df,
        oi_series=oi_series,
        vol_lookback=60,
        vol_pct=0.80,
        oi_min_trend=0.003,
        oi_min_squeeze=-0.005,
        df_liq=df_liq,
        price=ref_price,
        tick=tick,
    ) if REQUIRE_BOS_QUALITY else {"ok": True}
    bos_ok_q = bool(bos_details.get("ok", True))

    cvd_series = _get_series(signal, "cvd_series")
    comm = float(commitment_score(oi_series, cvd_series) or 0.0)  # 0..1

    # ------------- RR garde-fou (avec construction EXITS si besoin) -------------
    rr = _safe_rr(signal)
    if rr is None or not math.isfinite(rr) or rr <= 0:
        base = {
            "valid": False,
            "score": 50,
            "rr": None,
            "reasons": ["RR invalide (entry/SL/TP incohérents ou introuvables)"],
            "institutional": inst,
            "exits": signal.get("exits", {}),
        }
        if isinstance(struct_ctx, dict):
            base.update({
                "bos_direction": struct_ctx.get("bos_direction"),
                "choch_direction": struct_ctx.get("choch_direction"),
                "trend": struct_ctx.get("trend_state"),
                "phase": struct_ctx.get("phase"),
                "last_event": struct_ctx.get("last_event"),
                "cos": struct_ctx.get("cos"),
            })
        return base

    reasons: list[str] = []

    # ------------- Règle A0 : Desk EV priority (RR réduit, structure/momentum/BOS SOFT) -------------
    if (
        DESK_EV_MODE
        and inst_score >= int(INST_SCORE_DESK_PRIORITY)
        and comm >= float(COMMITMENT_DESK_PRIORITY)
    ):
        rr_floor = float(RR_MIN_DESK_PRIORITY)

        # Hard filters en Desk EV: RR mini + HTF
        if rr >= rr_floor and htf_ok:
            reasons_ev = [
                f"Desk EV priority: inst {inst_score}/3, commitment {comm:.2f}, RR {rr:.2f} ≥ {rr_floor:.2f}"
            ]
            # Structure / momentum / BOS deviennent SOFT : on loggue mais on ne bloque pas
            if not struct_ok:
                reasons_ev.append("Structure locale faible (tolérée en Desk EV)")
            if not mom_ok:
                reasons_ev.append("Momentum faible (toléré en Desk EV)")
            if not bos_ok_q:
                reasons_ev.append("Break faible (vol/OI) (toléré en Desk EV)")

            out = {
                "valid": True,
                "score": 105 + inst_score * 6 + int(25 * comm),
                "rr": float(rr),
                "reasons": reasons_ev,
                "institutional": inst,
                "exits": signal.get("exits", {}),
            }
            if isinstance(struct_ctx, dict):
                out.update({
                    "bos_direction": struct_ctx.get("bos_direction"),
                    "choch_direction": struct_ctx.get("choch_direction"),
                    "trend": struct_ctx.get("trend_state"),
                    "phase": struct_ctx.get("phase"),
                    "last_event": struct_ctx.get("last_event"),
                    "cos": struct_ctx.get("cos"),
                })
            if isinstance(bos_details, dict):
                out.update({
                    "bos_details": bos_details,
                    "has_liquidity_zone": bos_details.get("has_liquidity_zone"),
                    "liquidity_side": bos_details.get("liquidity_side"),
                })
            if "sl" in signal:
                out["sl"] = float(signal["sl"])
            if "tp1" in signal:
                out["tp1"] = float(signal["tp1"])
            return out
        else:
            # On garde la trace de pourquoi Desk EV n'a pas pu être appliqué
            if rr < rr_floor:
                reasons.append(f"RR {rr:.2f} < desk_EV_floor {rr_floor:.2f}")
            if not htf_ok:
                reasons.append("HTF non aligné (Desk EV)")

    # ------------- Règle A : institutionnel prioritaire (HTF hard, reste soft) -------------
    if inst_score >= MIN_INST_SCORE and comm >= COMMITMENT_MIN and rr >= RR_MIN_TOLERATED_WITH_INST:
        if htf_ok:
            reasons_a = [f"Institutionnel {inst_score}/3 + commitment {comm:.2f}"]
            if not struct_ok:
                reasons_a.append("Structure locale faible (tolérée insto)")
            if not mom_ok:
                reasons_a.append("Momentum faible (toléré insto)")
            if not bos_ok_q:
                reasons_a.append("Break faible (vol/OI) (toléré insto)")

            out = {
                "valid": True,
                "score": 100 + inst_score * 5 + int(20 * comm),
                "rr": float(rr),
                "reasons": reasons_a,
                "institutional": inst,
                "exits": signal.get("exits", {}),
            }
            if isinstance(struct_ctx, dict):
                out.update({
                    "bos_direction": struct_ctx.get("bos_direction"),
                    "choch_direction": struct_ctx.get("choch_direction"),
                    "trend": struct_ctx.get("trend_state"),
                    "phase": struct_ctx.get("phase"),
                    "last_event": struct_ctx.get("last_event"),
                    "cos": struct_ctx.get("cos"),
                })
            if isinstance(bos_details, dict):
                out.update({
                    "bos_details": bos_details,
                    "has_liquidity_zone": bos_details.get("has_liquidity_zone"),
                    "liquidity_side": bos_details.get("liquidity_side"),
                })
            if "sl" in signal:
                out["sl"] = float(signal["sl"])
            if "tp1" in signal:
                out["tp1"] = float(signal["tp1"])
            return out
        else:
            reasons.append("HTF non aligné")

    # ------------- Règle B : strict (technique+RR) -------------
    if rr >= RR_MIN_STRICT and struct_ok and mom_ok and htf_ok and bos_ok_q:
        out = {
            "valid": True,
            "score": 95 + inst_score * 3 + int(10 * comm),
            "rr": float(rr),
            "reasons": [f"RR≥{RR_MIN_STRICT} et technique OK"],
            "institutional": inst,
            "exits": signal.get("exits", {}),
        }
        if isinstance(struct_ctx, dict):
            out.update({
                "bos_direction": struct_ctx.get("bos_direction"),
                "choch_direction": struct_ctx.get("choch_direction"),
                "trend": struct_ctx.get("trend_state"),
                "phase": struct_ctx.get("phase"),
                "last_event": struct_ctx.get("last_event"),
                "cos": struct_ctx.get("cos"),
            })
        if isinstance(bos_details, dict):
            out.update({
                "bos_details": bos_details,
                "has_liquidity_zone": bos_details.get("has_liquidity_zone"),
                "liquidity_side": bos_details.get("liquidity_side"),
            })
        if "sl" in signal:
            out["sl"] = float(signal["sl"])
        if "tp1" in signal:
            out["tp1"] = float(signal["tp1"])
        return out

    # ------------- Rejet : raisons détaillées -------------
    if rr < RR_MIN_TOLERATED_WITH_INST:
        reasons.append(f"RR {rr:.2f} < toléré {RR_MIN_TOLERATED_WITH_INST}")
    elif rr < RR_MIN_STRICT:
        reasons.append(f"RR {rr:.2f} < strict {RR_MIN_STRICT}")

    if REQUIRE_STRUCTURE and not struct_ok:
        reasons.append("Structure invalide")
    if REQUIRE_MOMENTUM and not mom_ok:
        reasons.append("Momentum faible")
    if REQUIRE_HTF_ALIGN and not htf_ok:
        reasons.append("HTF non aligné")
    if REQUIRE_BOS_QUALITY and not bos_ok_q:
        reasons.append("Break faible (vol/OI)")

    ote = bool(signal.get("ote", True))
    if not ote:
        reasons.append("OTE manquant (toléré)")

    base = {
        "valid": False,
        "score": 50 + inst_score * 2 + int(10 * comm),
        "rr": float(rr),
        "reasons": reasons,
        "institutional": inst,
        "exits": signal.get("exits", {}),
    }
    if "sl" in signal:
        base["sl"] = float(signal["sl"])
    if "tp1" in signal:
        base["tp1"] = float(signal["tp1"])
    if isinstance(struct_ctx, dict):
        base.update({
            "bos_direction": struct_ctx.get("bos_direction"),
            "choch_direction": struct_ctx.get("choch_direction"),
            "trend": struct_ctx.get("trend_state"),
            "phase": struct_ctx.get("phase"),
            "last_event": struct_ctx.get("last_event"),
            "cos": struct_ctx.get("cos"),
        })
    if isinstance(bos_details, dict):
        base.update({
            "bos_details": bos_details.get("bos_details") if isinstance(bos_details, dict) else bos_details,
            "has_liquidity_zone": bos_details.get("has_liquidity_zone"),
            "liquidity_side": bos_details.get("liquidity_side"),
        })
    return base
