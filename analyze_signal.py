"""
analyze_signal.py — intégration institutionnelle + EXITS desk pro + garde-fou RR + HTF/BOS quality/Commitment

Nouveautés:
- Auto-calcul des EXITS si manquants:
    SL: stops.protective_stop_long/short (liquidité > structure > ATR) avec meta + log line compacte
    TP1: tp_clamp.compute_tp1 (clamp dynamique par régime de volatilité) avec meta
- RR recalculé à partir de (entry, sl, tp1) si rr_estimated invalide/manquant
- Priorité institutionnelle (MIN_INST_SCORE) + tolérance RR si inst OK et commitment OK
- Contrôles supplémentaires: HTF alignment, BOS quality (vol+OI), commitment score (OI + CVD)
"""
from __future__ import annotations
import math
from typing import Optional, Dict, Any, Tuple
import logging
import pandas as pd

LOGGER = logging.getLogger(__name__)

# Techniques & structure
from indicators import compute_rsi, compute_macd, compute_ema, compute_atr, is_momentum_ok
from structure_utils import structure_valid, htf_trend_ok, bos_quality_ok, commitment_score

# EXITS (SL + TP1 dynamique)
from stops import (
    protective_stop_long,
    protective_stop_short,
    format_sl_meta_for_log,
)
from tp_clamp import compute_tp1

# Institutionnel
from institutional_data import compute_full_institutional_analysis
# (optionnel, conservé si tu l'utilises quelque part ailleurs)
try:
    from institutional_data import detect_liquidity_clusters  # noqa: F401
except Exception:
    pass

# Settings (avec valeurs de secours)
from settings import (
    MIN_INST_SCORE, REQUIRE_STRUCTURE, REQUIRE_MOMENTUM,
    RR_MIN_STRICT, RR_MIN_TOLERATED_WITH_INST
)
try:
    from settings import REQUIRE_HTF_ALIGN
except Exception:
    REQUIRE_HTF_ALIGN = True

try:
    from settings import REQUIRE_BOS_QUALITY
except Exception:
    REQUIRE_BOS_QUALITY = True

try:
    from settings import COMMITMENT_MIN
except Exception:
    COMMITMENT_MIN = 0.55  # seuil 0..1


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


def _build_exits_if_needed(signal: Dict[str, Any]) -> Tuple[Optional[float], Optional[float], Dict[str, Any]]:
    """
    Construit SL/TP1 si manquants dans le signal.
    Attend dans le signal:
      - 'df': DataFrame H1
      - 'entry': float
      - 'tick': float (size)  -> requis pour un calcul propre (surtout SL)
      - facultatif: 'df_liq' ou 'df_m15' pour la liquidité M15
      - 'side' / 'bias' ("LONG"/"SHORT")

    Retourne: (sl, tp1, exits_dict)
      exits_dict contient: sl_meta, sl_log, tp1_meta si calculés, sinon {}
    """
    exits: Dict[str, Any] = {}
    df = signal.get("df")
    if not isinstance(df, pd.DataFrame) or "close" not in df:
        return None, None, exits

    side = str(signal.get("side", signal.get("bias", "LONG"))).lower()
    if side not in ("long", "short"):
        side = "long"

    entry = signal.get("entry", None)
    tick = signal.get("tick", 0.0)
    if entry is None:
        return None, None, exits
    try:
        entry = float(entry)
        tick = float(tick or 0.0)
    except Exception:
        return None, None, exits

    # Exits éventuellement déjà fournis
    sl = signal.get("sl", None)
    tp1 = signal.get("tp1", None)

    df_liq = signal.get("df_liq") or signal.get("df_m15")  # M15 recommandé pour la liquidité

    # ---- SL ----
    if sl is None:
        try:
            if side == "long":
                sl_val, sl_meta = protective_stop_long(df, entry, tick, df_liq=df_liq, return_meta=True)
            else:
                sl_val, sl_meta = protective_stop_short(df, entry, tick, df_liq=df_liq, return_meta=True)
            sl = float(sl_val)
            exits["sl_meta"] = sl_meta
            exits["sl_log"] = format_sl_meta_for_log(sl_meta)
            LOGGER.info("[EXITS][SL] %s | entry=%.12f | sl=%.12f", exits["sl_log"], entry, sl)
        except Exception as e:
            LOGGER.exception("SL build failed: %s", e)
            sl = None
    else:
        try:
            sl = float(sl)
        except Exception:
            sl = None

    # ---- TP1 ----
    if tp1 is None and sl is not None:
        try:
            tp1_val, tp1_meta = compute_tp1(
                df=df,
                entry=float(entry),
                sl=float(sl),
                side=side,
                tick=float(tick),
                rr_base=signal.get("rr_target"),  # fallback interne sur settings.RR_TARGET
                return_meta=True
            )
            tp1 = float(tp1_val)
            exits["tp1_meta"] = tp1_meta
            LOGGER.info(
                "[EXITS][TP1] side=%s entry=%.12f sl=%.12f rr_base=%s rr_eff=%.4f regime=%s -> tp1=%.12f",
                side, float(entry), float(sl),
                str(tp1_meta.get('rr_base')), float(tp1_meta.get('rr_effective', 0.0)),
                str(tp1_meta.get('regime')), float(tp1)
            )
        except Exception as e:
            LOGGER.exception("TP1 build failed: %s", e)
            tp1 = None
    else:
        try:
            tp1 = float(tp1) if tp1 is not None else None
        except Exception:
            tp1 = None

    exits["sl"] = sl
    exits["tp1"] = tp1
    return sl, tp1, exits


def _safe_rr(signal: Dict[str, Any]) -> Optional[float]:
    """
    Tente de produire un RR utilisable :
      1) utilise rr_estimated si valable
      2) sinon recalcule: RR = |tp1-entry| / |entry - sl|
      3) si sl/tp1 manquants, tente de les construire (EXITS) et recalcule
    Retourne None si impossible/invalide.
    """
    # 1) rr_estimated s'il est fourni et valide
    rr = signal.get("rr_estimated", None)
    if rr is not None:
        try:
            rr = float(rr)
            if math.isfinite(rr) and rr > 0:
                return rr
        except Exception:
            pass

    # 2) calcul à partir de données disponibles
    def _compute_rr_from(signal_dict: Dict[str, Any]) -> Optional[float]:
        try:
            entry = float(signal_dict.get("entry"))
            sl = float(signal_dict.get("sl"))
            tp1 = float(signal_dict.get("tp1"))
            r = abs(entry - sl)
            if r <= 0:
                return None
            rr_calc = abs(tp1 - entry) / r
            return rr_calc if math.isfinite(rr_calc) and rr_calc > 0 else None
        except Exception:
            return None

    rr2 = _compute_rr_from(signal)
    if rr2 is not None:
        return rr2

    # 3) Si on peut, construit les EXITS et recalcule
    sl_built, tp1_built, exits = _build_exits_if_needed(signal)
    if sl_built is not None and tp1_built is not None:
        tmp = {
            **signal,
            "sl": float(sl_built),
            "tp1": float(tp1_built),
        }
        rr3 = _compute_rr_from(tmp)
        if rr3 is not None:
            # injecte les exits calculés dans le signal pour le downstream éventuel
            signal.setdefault("exits", {}).update(exits)
            signal["sl"] = float(sl_built)
            signal["tp1"] = float(tp1_built)
            return rr3

    return None


# ----------------------------- Évaluation --------------------------------
def evaluate_signal(signal: Dict[str, Any]) -> Dict[str, Any]:
    """
    signal attendu:
      {
        symbol, bias('LONG'/'SHORT'),
        rr_estimated: float (optionnel),
        df: DataFrame(1H), entry, sl, tp1, tp2, ote: bool,
        # optionnels:
        df_h4: DataFrame(4H) pour HTF align,
        df_liq/df_m15: DataFrame(M15) pour la liquidité,
        tick: float,
        oi_series: pd.Series (OI), cvd_series: pd.Series (CVD),
        rr_target: float (optionnel) pour TP1 dynamique
      }

    Politique d'acceptation (desk pro):
      A) Institutionnel prioritaire:
         - inst_score >= MIN_INST_SCORE
         - commitment_score >= COMMITMENT_MIN
         - RR >= RR_MIN_TOLERATED_WITH_INST
         - + structure/momentum/HTF/BOS-quality selon REQUIRE_*
      B) Sinon stricte:
         - RR >= RR_MIN_STRICT
         - + structure/momentum/HTF/BOS-quality selon REQUIRE_*
      * OTE manquant = toléré (signal noté, pas bloquant)
    """
    # ------------- Données de base -------------
    symbol = signal.get("symbol")
    bias = str(signal.get("bias", signal.get("side", "LONG"))).upper()
    df = signal.get("df")
    if not isinstance(df, pd.DataFrame) or "close" not in df:
        return {
            "valid": False,
            "score": 0,
            "rr": None,
            "reasons": ["DF introuvable ou invalide"],
            "institutional": {"institutional_score": 0, "neutral": True, "reason": "no_df"}
        }

    close = df["close"]
    vol = df["volume"] if "volume" in df else None

    # ------------- Institutionnel (tolérant) -------------
    inst = _inst_or_neutral(symbol, bias)
    inst_score = int(inst.get("institutional_score", 0))

    # ------------- Techniques -------------
    struct_ok = structure_valid(df, bias) if REQUIRE_STRUCTURE else True
    mom_ok = (is_momentum_ok(close, vol) if vol is not None else True) if REQUIRE_MOMENTUM else True

    # ------------- Garde-fous insto étendus -------------
    df_h4 = signal.get("df_h4")
    htf_ok = htf_trend_ok(df_h4, bias) if REQUIRE_HTF_ALIGN else True

    oi_series = _get_series(signal, "oi_series")
    bos_ok_q = bos_quality_ok(df, oi_series) if REQUIRE_BOS_QUALITY else True

    cvd_series = _get_series(signal, "cvd_series")
    comm = float(commitment_score(oi_series, cvd_series) or 0.0)  # 0..1

    # ------------- RR garde-fou (avec construction EXITS si besoin) -------------
    rr = _safe_rr(signal)
    if rr is None or not math.isfinite(rr) or rr <= 0:
        return {
            "valid": False,
            "score": 50,
            "rr": None,
            "reasons": ["RR invalide (entry/SL/TP incohérents ou introuvables)"],
            "institutional": inst,
            "exits": signal.get("exits", {})
        }

    reasons = []

    # ------------- Règle A : institutionnel prioritaire -------------
    if inst_score >= MIN_INST_SCORE and comm >= COMMITMENT_MIN and rr >= RR_MIN_TOLERATED_WITH_INST:
        if struct_ok and mom_ok and htf_ok and bos_ok_q:
            out = {
                "valid": True,
                "score": 100 + inst_score * 5 + int(20 * comm),
                "rr": float(rr),
                "reasons": [f"Institutionnel {inst_score}/3 + commitment {comm:.2f}"],
                "institutional": inst,
                "exits": signal.get("exits", {}),
            }
            # propage SL/TP éventuels si présents (utile au reste du pipeline)
            if "sl" in signal: out["sl"] = float(signal["sl"])
            if "tp1" in signal: out["tp1"] = float(signal["tp1"])
            return out
        # expliquer ce qui manque
        if not struct_ok: reasons.append("Structure invalide")
        if not mom_ok: reasons.append("Momentum faible")
        if not htf_ok: reasons.append("HTF non aligné")
        if not bos_ok_q: reasons.append("Break faible (vol/OI)")

    # ------------- Règle B : stricte (technique+RR) -------------
    if rr >= RR_MIN_STRICT and struct_ok and mom_ok and htf_ok and bos_ok_q:
        out = {
            "valid": True,
            "score": 95 + inst_score * 3 + int(10 * comm),
            "rr": float(rr),
            "reasons": [f"RR≥{RR_MIN_STRICT} et technique OK"],
            "institutional": inst,
            "exits": signal.get("exits", {}),
        }
        if "sl" in signal: out["sl"] = float(signal["sl"])
        if "tp1" in signal: out["tp1"] = float(signal["tp1"])
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

    return {
        "valid": False,
        "score": 50 + inst_score * 2 + int(10 * comm),
        "rr": float(rr),
        "reasons": reasons,
        "institutional": inst,
        "exits": signal.get("exits", {}),
        **({"sl": float(signal["sl"])} if "sl" in signal else {}),
        **({"tp1": float(signal["tp1"])} if "tp1" in signal else {}),
    }
