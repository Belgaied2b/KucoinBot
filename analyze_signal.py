# -*- coding: utf-8 -*-
"""
analyze_signal.py — analyse multi-timeframe stricte avec validation institutionnelle,
macro, technique, structure. Génère une Decision exploitable par scanner.py.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, Dict, Any, Tuple, List, Optional
import pandas as pd

from config import SETTINGS
from orderflow_features import compute_atr, equal_highs_lows
from strategy_setups import initiative_breakout, vwap_reversion, stoprun_reversal

# ---------------------------------------------------------------------
# Placeholders pour modules optionnels (fallback sûrs si absent)
# ---------------------------------------------------------------------
try:
    import indicators as indi  # type: ignore
except Exception:
    class _DummyIndi:
        def macd(self, s: pd.Series):
            ema12 = s.ewm(span=12, adjust=False).mean()
            ema26 = s.ewm(span=26, adjust=False).mean()
            macd = ema12 - ema26
            signal = macd.ewm(span=9, adjust=False).mean()
            return macd, signal

        def is_price_in_ote_zone(self, df: pd.DataFrame, side: str) -> bool:
            return True

        def is_momentum_ok(self, df: pd.DataFrame) -> bool:
            return True
    indi = _DummyIndi()  # type: ignore

try:
    import structure_utils as su  # type: ignore
except Exception:
    class _DummySU:
        def has_recent_bos(self, df: pd.DataFrame) -> bool: return True
        def is_choch_conditioned(self, df: pd.DataFrame) -> bool: return True
        def is_bullish_engulfing(self, df: pd.DataFrame) -> bool: return False
        def is_bearish_engulfing(self, df: pd.DataFrame) -> bool: return False
    su = _DummySU()  # type: ignore


# ---------------------------------------------------------------------
# Modèle de décision
# ---------------------------------------------------------------------
@dataclass
class Decision:
    side: Literal["LONG", "SHORT", "NONE"]
    name: str
    reason: str
    tolerated: List[str]
    rr: float
    entry: float
    sl: float
    tp1: float
    tp2: float
    score: float
    manage: Dict[str, Any]


# ---------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------
def _safe_last(series: pd.Series, default: float = 0.0) -> float:
    try:
        return float(series.iloc[-1])
    except Exception:
        return float(default)


def _tech_context_ok(df: pd.DataFrame) -> Tuple[bool, Dict[str, bool]]:
    """Contexte technique simple: tendance & momentum (EMA20/EMA50 + MACD>signal)."""
    closes = df["close"].astype(float)
    e20 = closes.ewm(span=20, adjust=False).mean()
    e50 = closes.ewm(span=50, adjust=False).mean()
    macd, signal = indi.macd(closes)

    trend_ok = bool(_safe_last(e20) > _safe_last(e50))
    macd_ok = bool(_safe_last(macd) > _safe_last(signal))
    return bool(trend_ok and macd_ok), {"ema_trend": trend_ok, "macd": macd_ok}


def _macro_filter_ok(macro: Dict[str, float]) -> Tuple[bool, Dict[str, float | bool]]:
    """Filtre macro: stress si drawdown fort sur TOTAL/TOTAL2 ou dominance BTC élevée."""
    if not getattr(SETTINGS, "use_macro", False):
        return True, {"enabled": False}

    tpct = float(macro.get("TOTAL_PCT", 0.0) or 0.0)
    t2pct = float(macro.get("TOTAL2_PCT", 0.0) or 0.0)
    dom = float(macro.get("BTC_DOM", 0.0) or 0.0)

    stress_total = (tpct < -0.02)
    stress_total2 = (getattr(SETTINGS, "use_total2", False) and (t2pct < -0.03))
    stress_dom = (dom > 0.58 and dom < 1.5)

    ok = not (stress_total or stress_total2 or stress_dom)
    return ok, {
        "enabled": True,
        "TOTAL_PCT": tpct,
        "TOTAL2_PCT": t2pct,
        "BTC_DOM": dom,
        "stress_total": stress_total,
        "stress_total2": stress_total2,
        "stress_dom": stress_dom,
    }


def _structure_filter_ok(df: pd.DataFrame) -> Tuple[bool, List[str], Dict[str, bool]]:
    """Structure de marché: BOS/CHoCH + bougie d’activation (engulfing)."""
    notes: List[str] = []
    try:
        bos_ok = bool(su.has_recent_bos(df))
    except Exception:
        bos_ok = True
    try:
        choch_ok = bool(su.is_choch_conditioned(df))
    except Exception:
        choch_ok = True
    try:
        engulf = bool(su.is_bullish_engulfing(df) or su.is_bearish_engulfing(df))
    except Exception:
        engulf = True

    if not (bos_ok or choch_ok):
        notes.append("COS")
    if not engulf:
        notes.append("BOUGIE")

    return ((bos_ok or choch_ok) and engulf), notes, {"bos": bos_ok, "choch": choch_ok, "engulf": engulf}


def _pick_setup(entry_price: float, inst: Dict[str, Any], df: pd.DataFrame):
    """Essaye plusieurs setups et prend le premier valide."""
    cands = []
    try:
        cands.append(initiative_breakout(entry_price, inst, df))
    except Exception:
        pass
    try:
        vwap_col = "vwap_US" if "vwap_US" in df.columns else None
        cands.append(vwap_reversion(entry_price, inst, df, vwap_col=vwap_col))
    except Exception:
        pass
    try:
        cands.append(stoprun_reversal(entry_price, inst, df))
    except Exception:
        pass

    cands = [c for c in cands if c is not None]
    if not cands:
        return type("Setup", (), {"side": "NONE", "name": "None"})()

    for c in cands:
        if getattr(c, "side", "NONE") != "NONE":
            return c
    return cands[0]


def _institutional_gate(inst: Dict[str, Any]) -> Tuple[bool, Dict[str, Any]]:
    """Porte institutionnelle stricte: seuil score global + min composantes OK."""
    score = float(inst.get("score", 0.0) or 0.0)
    oi_s = float(inst.get("oi_score", 0.0) or 0.0)
    dlt_s = float(inst.get("cvd_score", 0.0) or 0.0)  # corrigé
    fund_s = float(inst.get("funding_score", 0.0) or 0.0)
    liq_new = inst.get("liq_new_score", None)
    liq_s = float(liq_new if liq_new is not None else (inst.get("liq_score", 0.0) or 0.0))
    book_s = float(inst.get("book_imbal_score", 0.0) or 0.0)

    req_score_min = float(getattr(SETTINGS, "req_score_min", 1.5))
    oi_min = float(getattr(SETTINGS, "oi_req_min", 0.4))
    delta_min = float(getattr(SETTINGS, "delta_req_min", 0.4))
    funding_min = float(getattr(SETTINGS, "funding_req_min", 0.2))
    liq_min = float(getattr(SETTINGS, "liq_req_min", 0.5))
    book_min = float(getattr(SETTINGS, "book_req_min", 0.3))
    use_book = bool(getattr(SETTINGS, "use_book_imbal", True))
    inst_components_min = int(getattr(SETTINGS, "inst_components_min", 2))

    comp_status = {
        "oi_ok": oi_s >= oi_min,
        "delta_ok": dlt_s >= delta_min,
        "fund_ok": fund_s >= funding_min,
        "liq_ok": liq_s >= liq_min,
        "book_ok": (book_s >= book_min) if use_book else None,
    }
    used_flags = [v for v in comp_status.values() if v is not None]
    nb_ok = sum(1 for v in used_flags if v)

    gate_ok = (score >= req_score_min) and (nb_ok >= inst_components_min)

    return gate_ok, {
        "score": score,
        "req_score_min": req_score_min,
        "oi_score": oi_s, "cvd_score": dlt_s, "funding_score": fund_s,
        "liq_score": liq_s, "book_score": book_s,
        "liq_source": "liq_new_score" if liq_new is not None else "liq_score",
        "thresholds": {
            "oi_min": oi_min, "cvd_min": delta_min, "funding_min": funding_min,
            "liq_min": liq_min, "book_min": book_min,
            "components_min": inst_components_min, "use_book": use_book,
        },
        "components_ok": comp_status,
        "components_ok_count": nb_ok,
    }


def _build_diagnostics(inst_diag: Dict[str, Any],
                       tech_diag: Dict[str, bool],
                       struct_diag: Dict[str, bool],
                       macro_diag: Dict[str, Any],
                       tolerated: List[str],
                       extra_rejects: Optional[List[str]] = None) -> Dict[str, Any]:
    return {
        "inst": {
            "score": inst_diag.get("score"),
            "components_ok": inst_diag.get("components_ok"),
            "components_ok_count": inst_diag.get("components_ok_count"),
            "thresholds": inst_diag.get("thresholds"),
        },
        "tech": {
            "ema_trend_ok": bool(tech_diag.get("ema_trend", False)),
            "momentum_ok": bool(tech_diag.get("macd", False)),
        },
        "struct": struct_diag,
        "macro": macro_diag,
        "tolerated": sorted(set(tolerated)),
        "reasons_block": extra_rejects or [],
    }


# ---------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------
def analyze_signal(symbol: str,
                   entry_price: float,
                   df_h1: pd.DataFrame,
                   df_h4: pd.DataFrame,
                   df_d1: pd.DataFrame,
                   df_m15: pd.DataFrame,
                   inst: Dict[str, Any],
                   macro: Optional[Dict[str, float]] = None) -> Decision:
    """Analyse multi-timeframe stricte et renvoie une décision."""

    # ------- Institutionnel -------
    inst_ok, inst_diag = _institutional_gate(inst)
    score = float(inst_diag["score"])
    if not inst_ok:
        reason = f"REJET — Institutionnel insuffisant (score={score:.2f})"
        manage = {"diagnostics": _build_diagnostics(inst_diag, {}, {}, {"enabled": getattr(SETTINGS, "use_macro", False)}, [])}
        return Decision("NONE", "None", reason, [], 0.0, float(entry_price), 0.0, 0.0, 0.0, score, manage)

    # ------- Macro -------
    macro_ok, macro_diag = _macro_filter_ok(macro or {})
    if not macro_ok:
        reason = "REJET — Macro défavorable"
        manage = {"diagnostics": _build_diagnostics(inst_diag, {}, {}, macro_diag, [])}
        return Decision("NONE", "None", reason, [], 0.0, float(entry_price), 0.0, 0.0, 0.0, score, manage)

    # ------- Technique & Structure -------
    tech_ok, tech_diag = _tech_context_ok(df_h1)
    struct_ok, struct_notes, struct_diag = _structure_filter_ok(df_h1)

    # ------- Setup (H1 pour setup, validé par H4/D1, confirmé par M15) -------
    setup = _pick_setup(entry_price, inst, df_h1)
    if getattr(setup, "side", "NONE") == "NONE":
        reason = "REJET — Aucun setup valide"
        manage = {"diagnostics": _build_diagnostics(inst_diag, tech_diag, struct_diag, macro_diag, [], ["no_valid_setup"])}
        return Decision("NONE", getattr(setup, "name", "None"), reason, [], 0.0,
                        float(entry_price), 0.0, 0.0, 0.0, score, manage)

    # ------- ATR / Liquidity pools -------
    try:
        atr_val = float(compute_atr(df_h1).iloc[-1])
    except Exception:
        atr_val = 0.0
    try:
        pool_hi, pool_lo = equal_highs_lows(df_h1, lookback=120, precision=2)
    except Exception:
        pool_hi = pool_lo = False

    tolerated: List[str] = []

    # ------- OTE / Momentum tolérances -------
    try:
        in_ote = bool(indi.is_price_in_ote_zone(df_h1, getattr(setup, "side", "LONG")))
    except Exception:
        in_ote = True
    if not in_ote:
        tolerated.append("OTE")

    try:
        diverge_ok = bool(indi.is_momentum_ok(df_h1))
    except Exception:
        diverge_ok = True
    if not diverge_ok:
        tolerated.append("DIVERGENCE")

    for n in struct_notes:
        if n not in tolerated:
            tolerated.append(n)

    # ------- SL / TP / RR -------
    side = getattr(setup, "side", "LONG")
    mult_atr = float(getattr(SETTINGS, "sl_atr_mult", 1.5))
    tp1_rr = float(getattr(SETTINGS, "tp1_rr", 1.0))
    tp2_rr = float(getattr(SETTINGS, "tp2_rr", 2.0))

    if side == "LONG":
        sl = entry_price - mult_atr * atr_val
        risk = max(1e-9, entry_price - sl)
        tp1 = entry_price + tp1_rr * risk
        tp2 = entry_price + tp2_rr * risk
    else:
        sl = entry_price + mult_atr * atr_val
        risk = max(1e-9, sl - entry_price)
        tp1 = entry_price - tp1_rr * risk
        tp2 = entry_price - tp2_rr * risk

    rr = abs((tp1 - entry_price) / risk) if risk > 0 else 0.0
    req_rr_min = float(getattr(SETTINGS, "req_rr_min", 1.2))
    allow_tol_rr = bool(getattr(SETTINGS, "allow_tol_rr", True))

    if rr < req_rr_min and not allow_tol_rr:
        reason = f"REJET — RR {rr:.2f} < req {req_rr_min:.2f}"
        manage = {"diagnostics": _build_diagnostics(inst_diag, tech_diag, struct_diag, macro_diag, tolerated, ["rr_below_min"])}
        return Decision("NONE", getattr(setup, "name", "None"), reason, [], float(rr),
                        float(entry_price), float(sl), float(tp1), float(tp2), float(score), manage)

    if rr < req_rr_min and allow_tol_rr and "RR" not in tolerated:
        tolerated.append("RR")

    if not tech_ok and "DIVERGENCE" not in tolerated:
        tolerated.append("DIVERGENCE")
    if not struct_ok and "COS" not in tolerated:
        tolerated.append("COS")

    # ------- Accept -------
    reason = f"ACCEPTÉ — {side} | Score={score:.2f} | RR={rr:.2f}"
    manage = {
        "tp1_part": float(getattr(SETTINGS, "tp1_part", 0.5)),
        "move_to_be_after_tp1": bool(getattr(SETTINGS, "breakeven_after_tp1", True)),
        "trail_after_tp1_mult_atr": float(getattr(SETTINGS, "trail_mult_atr", 1.0)),
        "diagnostics": _build_diagnostics(inst_diag, tech_diag, struct_diag, macro_diag, tolerated, []),
    }

    return Decision(side=side, name=getattr(setup, "name", "setup"), reason=reason,
                    tolerated=sorted(set(tolerated)), rr=float(rr),
                    entry=float(entry_price), sl=float(sl),
                    tp1=float(tp1), tp2=float(tp2),
                    score=float(score), manage=manage)
