# -*- coding: utf-8 -*-
"""
analyze_signal.py — analyse multi-timeframe stricte avec validation institutionnelle,
macro, technique, structure. Génère un dict exploitable par main/scanner (valid=True/False).
"""
from __future__ import annotations

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
    stress_total  = (tpct < -0.02)
    stress_total2 = (getattr(SETTINGS, "use_total2", False) and (t2pct < -0.03))
    stress_dom    = (dom > 0.58 and dom < 1.5)
    ok = not (stress_total or stress_total2 or stress_dom)
    return ok, {
        "enabled": True, "TOTAL_PCT": tpct, "TOTAL2_PCT": t2pct, "BTC_DOM": dom,
        "stress_total": stress_total, "stress_total2": stress_total2, "stress_dom": stress_dom,
    }

def _structure_filter_ok(df: pd.DataFrame) -> Tuple[bool, List[str], Dict[str, bool]]:
    """Structure de marché: BOS/CHoCH + bougie d’activation (engulfing)."""
    notes: List[str] = []
    try: bos_ok = bool(su.has_recent_bos(df))
    except Exception: bos_ok = True
    try: choch_ok = bool(su.is_choch_conditioned(df))
    except Exception: choch_ok = True
    try: engulf = bool(su.is_bullish_engulfing(df) or su.is_bearish_engulfing(df))
    except Exception: engulf = True
    if not (bos_ok or choch_ok): notes.append("COS")
    if not engulf: notes.append("BOUGIE")
    return ((bos_ok or choch_ok) and engulf), notes, {"bos": bos_ok, "choch": choch_ok, "engulf": engulf}

def _pick_setup(entry_price: float, inst: Dict[str, Any], df: pd.DataFrame):
    """Essaye plusieurs setups et prend le premier valide."""
    cands = []
    try: cands.append(initiative_breakout(entry_price, inst, df))
    except Exception: pass
    try:
        vwap_col = "vwap_US" if "vwap_US" in df.columns else None
        cands.append(vwap_reversion(entry_price, inst, df, vwap_col=vwap_col))
    except Exception: pass
    try: cands.append(stoprun_reversal(entry_price, inst, df))
    except Exception: pass
    cands = [c for c in cands if c is not None]
    if not cands:
        return type("Setup", (), {"side": "NONE", "name": "None"})()
    for c in cands:
        if getattr(c, "side", "NONE") != "NONE":
            return c
    return cands[0]

# ---------------------------------------------------------------------
# Gate institutionnel tolérant
# ---------------------------------------------------------------------
def _institutional_gate(inst: Dict[str, Any]) -> Tuple[bool, Dict[str, Any]]:
    score  = float(inst.get("score", 0.0) or 0.0)
    oi_s   = float(inst.get("oi_score", 0.0) or 0.0)
    cvd_s  = float(inst.get("cvd_score", 0.0) or 0.0)
    fund_s = float(inst.get("funding_score", 0.0) or 0.0)
    liq_s  = float(inst.get("liq_new_score", inst.get("liq_score", 0.0) or 0.0))
    book_s = float(inst.get("book_imbal_score", 0.0) or 0.0)

    req_score_min = float(getattr(SETTINGS, "req_score_min", 1.5))
    oi_min   = float(getattr(SETTINGS, "oi_req_min", 0.4))
    cvd_min  = float(getattr(SETTINGS, "delta_req_min", 0.4))
    fund_min = float(getattr(SETTINGS, "funding_req_min", 0.2))
    liq_min  = float(getattr(SETTINGS, "liq_req_min", 0.5))
    book_min = float(getattr(SETTINGS, "book_req_min", 0.3))
    use_book = bool(getattr(SETTINGS, "use_book_imbal", True))
    comps_min = int(getattr(SETTINGS, "inst_components_min", 2))

    comp_status = {
        "oi_ok": oi_s >= oi_min,
        "cvd_ok": cvd_s >= cvd_min,
        "fund_ok": fund_s >= fund_min,
        "liq_ok": liq_s >= liq_min,
        "book_ok": (book_s >= book_min) if use_book else None,
    }
    used = [v for v in comp_status.values() if v is not None]
    nb_ok = sum(1 for v in used if v)

    force_pass = nb_ok >= 4
    tol_pass   = (not force_pass) and (nb_ok >= 3)
    score_gate = (score >= req_score_min) and (nb_ok >= comps_min)
    gate_ok    = force_pass or tol_pass or score_gate

    return gate_ok, {
        "score": score,
        "components_ok": comp_status,
        "components_ok_count": nb_ok,
        "req_score_min": req_score_min,
        "thresholds": {
            "oi_min": oi_min, "cvd_min": cvd_min,
            "funding_min": fund_min, "liq_min": liq_min,
            "book_min": book_min, "components_min": comps_min,
            "use_book": use_book,
        },
        "reason": ("force_pass_4of4" if force_pass else
                   ("tolerance_pass_3of4" if tol_pass else
                    ("score_gate" if score_gate else "reject")))
    }

# ---------------------------------------------------------------------
# Fallback flow directionnel (InstitutionalFlow)
# ---------------------------------------------------------------------
def _fallback_institutional_flow(inst: Dict[str, Any], df_h1: pd.DataFrame) -> Literal["LONG", "SHORT", "NONE"]:
    """Vote simple: CVD, Funding, Tendance EMA20>EMA50."""
    closes = df_h1["close"].astype(float)
    e20 = closes.ewm(span=20, adjust=False).mean()
    e50 = closes.ewm(span=50, adjust=False).mean()
    trend_up = _safe_last(e20) > _safe_last(e50)

    cvd_s  = float(inst.get("cvd_score", 0.0) or 0.0)
    cvd_up = cvd_s >= float(getattr(SETTINGS, "delta_req_min", 0.4))

    fund_s  = float(inst.get("funding_score", 0.0) or 0.0)
    fund_up = fund_s >= float(getattr(SETTINGS, "funding_req_min", 0.2))

    votes_up = sum([trend_up, cvd_up, fund_up])
    votes_dn = 3 - votes_up

    if votes_up >= 2: return "LONG"
    if votes_dn >= 2: return "SHORT"
    return "NONE"

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
                   macro: Optional[Dict[str, float]] = None) -> Dict[str, Any]:
    """Analyse multi-timeframe stricte et renvoie un dict exploitable (avec valid=True/False)."""

    # ------- Institutionnel -------
    inst_ok, inst_diag = _institutional_gate(inst)
    score = float(inst_diag["score"])

    if not inst_ok:
        return {
            "valid": False, "side": "none", "rr": 0.0, "entry": float(entry_price),
            "sl": 0.0, "tp1": 0.0, "tp2": 0.0, "inst_score": score,
            "reason": f"REJET — Institutionnel insuffisant ({inst_diag.get('reason')})",
            "comments": ["institutional_gate_reject"],
            "manage": {"diagnostics": {"inst": inst_diag, "macro": {"enabled": getattr(SETTINGS, "use_macro", False)}}}
        }

    # ------- Macro -------
    macro_ok, macro_diag = _macro_filter_ok(macro or {})
    if not macro_ok:
        return {
            "valid": False, "side": "none", "rr": 0.0, "entry": float(entry_price),
            "sl": 0.0, "tp1": 0.0, "tp2": 0.0, "inst_score": score,
            "reason": "REJET — Macro défavorable",
            "comments": ["macro_reject"],
            "manage": {"diagnostics": {"inst": inst_diag, "macro": macro_diag}}
        }

    # ------- Technique & Structure -------
    tech_ok, tech_diag = _tech_context_ok(df_h1)
    struct_ok, struct_notes, struct_diag = _structure_filter_ok(df_h1)

    # ------- Setup pattern -------
    setup = _pick_setup(entry_price, inst, df_h1)
    setup_side = getattr(setup, "side", "NONE")
    setup_name = getattr(setup, "name", "None")

    # ------- ATR -------
    try:
        atr_val = float(compute_atr(df_h1).iloc[-1])
    except Exception:
        atr_val = 0.0

    # ------- Tolérances
    tolerated: List[str] = []
    try:
        in_ote = bool(indi.is_price_in_ote_zone(df_h1, setup_side if setup_side != "NONE" else "LONG"))
    except Exception:
        in_ote = True
    if not in_ote: tolerated.append("OTE")

    try:
        diverge_ok = bool(indi.is_momentum_ok(df_h1))
    except Exception:
        diverge_ok = True
    if not diverge_ok: tolerated.append("DIVERGENCE")
    for n in struct_notes:
        if n not in tolerated: tolerated.append(n)

    # ------- Choix du side + SL/TP/RR
    def _build_trade(side: Literal["LONG","SHORT"], entry: float) -> Dict[str, Any]:
        mult_atr = float(getattr(SETTINGS, "sl_atr_mult", 1.5))
        tp1_rr = float(getattr(SETTINGS, "tp1_rr", 1.0))
        tp2_rr = float(getattr(SETTINGS, "tp2_rr", 2.0))
        if side == "LONG":
            sl = entry - mult_atr * atr_val
            risk = max(1e-9, entry - sl)
            tp1 = entry + tp1_rr * risk
            tp2 = entry + tp2_rr * risk
        else:
            sl = entry + mult_atr * atr_val
            risk = max(1e-9, sl - entry)
            tp1 = entry - tp1_rr * risk
            tp2 = entry - tp2_rr * risk
        rr = abs((tp1 - entry) / risk) if risk > 0 else 0.0
        return {"sl": float(sl), "tp1": float(tp1), "tp2": float(tp2), "rr": float(rr)}

    req_rr_min = float(getattr(SETTINGS, "req_rr_min", 1.2))
    allow_tol_rr = bool(getattr(SETTINGS, "allow_tol_rr", True))

    if setup_side != "NONE":
        side = "LONG" if setup_side.upper()=="LONG" else "SHORT"
        trade = _build_trade(side, float(entry_price))
    else:
        # ===== Fallback InstitutionalFlow =====
        side_guess = _fallback_institutional_flow(inst, df_h1)
        if side_guess == "NONE":
            return {
                "valid": False, "side": "none", "rr": 0.0, "entry": float(entry_price),
                "sl": 0.0, "tp1": 0.0, "tp2": 0.0, "inst_score": score,
                "reason": "REJET — Aucun setup valide et vote flow neutre",
                "comments": ["no_valid_setup", "flow_neutral"],
                "manage": {"diagnostics": {"inst": inst_diag, "tech": tech_diag, "struct": struct_diag, "macro": macro_diag,
                                           "tolerated": tolerated}}
            }
        side = side_guess
        setup_name = "InstitutionalFlow"
        trade = _build_trade(side, float(entry_price))

    rr = trade["rr"]
    comments: List[str] = []
    if rr < req_rr_min:
        if allow_tol_rr:
            comments.append("RR_tol")
        else:
            return {
                "valid": False, "side": "none", "rr": float(rr), "entry": float(entry_price),
                "sl": float(trade["sl"]), "tp1": float(trade["tp1"]), "tp2": float(trade["tp2"]),
                "inst_score": score, "reason": f"REJET — RR {rr:.2f} < req {req_rr_min:.2f}",
                "comments": ["rr_below_min"] + comments,
                "manage": {"diagnostics": {"inst": inst_diag, "tech": tech_diag, "struct": struct_diag, "macro": macro_diag,
                                           "tolerated": tolerated}}
            }

    if not tech_ok: tolerated.append("DIVERGENCE")
    if not struct_ok: tolerated.append("COS")

    reason = f"ACCEPTÉ — {side} | Score={score:.2f} | RR={rr:.2f}"
    manage = {
        "tp1_part": float(getattr(SETTINGS, "tp1_part", 0.5)),
        "move_to_be_after_tp1": bool(getattr(SETTINGS, "breakeven_after_tp1", True)),
        "trail_after_tp1_mult_atr": float(getattr(SETTINGS, "trail_mult_atr", 1.0)),
        "diagnostics": {
            "inst": inst_diag, "tech": tech_diag, "struct": struct_diag, "macro": macro_diag,
            "tolerated": sorted(set(tolerated))
        }
    }

    return {
        "valid": True,
        "name": setup_name,
        "side": side.lower(),
        "reason": reason,
        "comments": comments,
        "rr": float(rr),
        "entry": float(entry_price),
        "sl": float(trade["sl"]),
        "tp1": float(trade["tp1"]),
        "tp2": float(trade["tp2"]),
        "inst_score": float(score),
        "manage": manage
    }
