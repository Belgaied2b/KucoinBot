# analyze_signal.py
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
        v = float(series.iloc[-1])
        return v
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

    total = float(macro.get("TOTAL", 0.0) or 0.0)  # non utilisé mais conservé
    total2 = float(macro.get("TOTAL2", 0.0) or 0.0)  # non utilisé mais conservé
    dom = float(macro.get("BTC_DOM", 0.0) or 0.0)
    tpct = float(macro.get("TOTAL_PCT", 0.0) or 0.0)
    t2pct = float(macro.get("TOTAL2_PCT", 0.0) or 0.0)

    stress_total = (tpct < -0.02)
    stress_total2 = (getattr(SETTINGS, "use_total2", False) and (t2pct < -0.03))
    # garde-fou si macro vide/buguée
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
        # vwap_col peut ne pas exister selon tes données -> on gère
        vwap_col = "vwap_US" if "vwap_US" in df.columns else None
        cands.append(vwap_reversion(entry_price, inst, df, vwap_col=vwap_col))
    except Exception:
        pass
    try:
        cands.append(stoprun_reversal(entry_price, inst, df))
    except Exception:
        pass

    # filtres None
    cands = [c for c in cands if c is not None]
    if not cands:
        return type("Setup", (), {"side": "NONE", "name": "None"})()

    for c in cands:
        if getattr(c, "side", "NONE") != "NONE":
            return c
    return cands[0]


def _institutional_gate(inst: Dict[str, Any]) -> Tuple[bool, Dict[str, Any]]:
    """
    Porte 'institutionnelle' :
    - utilise le score global (pondéré ailleurs)
    - exige un minimum de composantes OK (incluant liquidations via liq_new_score si dispo)
    Seuils/paramètres lisibles dans SETTINGS (valeurs par défaut si absents).
    """
    score = float(inst.get("score", 0.0) or 0.0)

    # Sous-scores
    oi_s = float(inst.get("oi_score", 0.0) or 0.0)
    dlt_s = float(inst.get("delta_score", 0.0) or 0.0)
    fund_s = float(inst.get("funding_score", 0.0) or 0.0)
    liq_new = inst.get("liq_new_score", None)
    liq_s = float(liq_new if liq_new is not None else (inst.get("liq_score", 0.0) or 0.0))
    book_s = float(inst.get("book_imbal_score", 0.0) or 0.0)

    # Seuils (défauts sûrs)
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
        "book_ok": (book_s >= book_min) if use_book else None,  # None = non utilisé
    }

    used_flags = [v for v in comp_status.values() if v is not None]
    nb_ok = sum(1 for v in used_flags if v)

    gate_ok = (score >= req_score_min) and (nb_ok >= inst_components_min)

    diag = {
        "score": score,
        "req_score_min": req_score_min,
        "oi_score": oi_s, "delta_score": dlt_s, "funding_score": fund_s,
        "liq_score": liq_s, "book_score": book_s,
        "liq_source": "liq_new_score" if liq_new is not None else "liq_score",
        "thresholds": {
            "oi_min": oi_min, "delta_min": delta_min, "funding_min": funding_min,
            "liq_min": liq_min, "book_min": book_min,
            "components_min": inst_components_min, "use_book": use_book,
        },
        "components_ok": comp_status,
        "components_ok_count": nb_ok,
    }
    return gate_ok, diag


def _build_diagnostics(
    inst_diag: Dict[str, Any],
    tech_diag: Dict[str, bool],
    struct_diag: Dict[str, bool],
    macro_diag: Dict[str, Any],
    tolerated: List[str],
    extra_rejects: Optional[List[str]] = None,
) -> Dict[str, Any]:
    """Construit un bloc diagnostics exploitable par le logger externe."""
    rejects = extra_rejects or []
    return {
        "inst": {
            "score": inst_diag.get("score"),
            "components_ok": inst_diag.get("components_ok"),
            "components_ok_count": inst_diag.get("components_ok_count"),
            "thresholds": inst_diag.get("thresholds"),
        },
        "tech": {
            "ema_trend_ok": bool(tech_diag.get("ema_trend", False)),
            "momentum_ok": bool(tech_diag.get("macd", False)),  # équiv. momentum simple
        },
        "struct": struct_diag,
        "macro": macro_diag,
        "tolerated": sorted(set(tolerated)),
        "reasons_block": rejects,
    }


# ---------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------
def analyze_signal(
    entry_price: float,
    df: pd.DataFrame,
    inst: Dict[str, Any],
    macro: Optional[Dict[str, float]] = None
) -> Decision:
    """
    Analyse un signal et renvoie une décision structurée.

    :param entry_price: prix d'entrée pressenti
    :param df: DataFrame OHLCV (colonnes: open, high, low, close, volume, ...)
    :param inst: dict indicateurs institutionnels (score global + sous-scores)
    :param macro: dict macro global (TOTAL/TOTAL2/BTC_DOM etc.)
    :return: Decision
    """

    # ------- Institutionnel -------
    inst_ok, inst_diag = _institutional_gate(inst)
    score = float(inst_diag["score"])
    oi_s = float(inst_diag["oi_score"])
    dlt_s = float(inst_diag["delta_score"])
    fund_s = float(inst_diag["funding_score"])
    liq_s = float(inst_diag["liq_score"])
    book_s = float(inst_diag["book_score"])
    liq_src = str(inst_diag["liq_source"])

    if not inst_ok:
        reason = (
            "REJET — Institutionnel insuffisant\n"
            f"Score={score:.2f} (req>={inst_diag['req_score_min']:.2f}) | "
            f"Composantes OK={inst_diag['components_ok_count']}/{inst_diag['thresholds']['components_min']}\n"
            f"OI={oi_s:.2f} Δ={dlt_s:.2f} fund={fund_s:.2f} liq[{liq_src}]={liq_s:.2f} "
            + (f"book={book_s:.2f}" if inst_diag['thresholds']['use_book'] else "book=NA") +
            f"\nSeuils: {inst_diag['thresholds']}\n"
            f"DetailsOK: {inst_diag['components_ok']}"
        )
        manage = {"diagnostics": _build_diagnostics(inst_diag, {}, {}, {"enabled": getattr(SETTINGS, "use_macro", False)}, [])}
        return Decision("NONE", "None", reason, [], 0.0, float(entry_price), 0.0, 0.0, 0.0, score, manage)

    # ------- Macro -------
    macro_ok, macro_diag = _macro_filter_ok(macro or {})
    if not macro_ok:
        reason = (
            "REJET — Filtre macro défavorable (TOTAL/TOTAL2/Dominance)\n"
            f"Macro: enabled={macro_diag.get('enabled')} "
            f"TOTAL_PCT={macro_diag.get('TOTAL_PCT')} TOTAL2_PCT={macro_diag.get('TOTAL2_PCT')} "
            f"BTC_DOM={macro_diag.get('BTC_DOM')} "
            f"[stress_total={macro_diag.get('stress_total')} stress_total2={macro_diag.get('stress_total2')} stress_dom={macro_diag.get('stress_dom')}]"
        )
        manage = {"diagnostics": _build_diagnostics(inst_diag, {}, {}, macro_diag, [])}
        return Decision("NONE", "None", reason, [], 0.0, float(entry_price), 0.0, 0.0, 0.0, score, manage)

    # ------- Technique & Structure -------
    tech_ok, tech_diag = _tech_context_ok(df)
    struct_ok, struct_notes, struct_diag = _structure_filter_ok(df)

    # ------- Setup -------
    setup = _pick_setup(entry_price, inst, df)
    if getattr(setup, "side", "NONE") == "NONE":
        reason = (
            f"REJET — Aucun setup valide ({getattr(setup, 'name', 'setup')})\n"
            f"Institutionnel: OI={oi_s:.2f} Δ={dlt_s:.2f} fund={fund_s:.2f} liq[{liq_src}]={liq_s:.2f} "
            + (f"book={book_s:.2f}" if inst_diag['thresholds']['use_book'] else "book=NA") + "\n"
            f"Technique: ema_trend={tech_diag['ema_trend']} macd={tech_diag['macd']}\n"
            f"Structure: bos={struct_diag['bos']} choch={struct_diag['choch']} engulf={struct_diag['engulf']}"
        )
        manage = {"diagnostics": _build_diagnostics(inst_diag, tech_diag, struct_diag, macro_diag, [], ["no_valid_setup"])}
        return Decision("NONE", getattr(setup, "name", "None"), reason, [], 0.0,
                        float(entry_price), 0.0, 0.0, 0.0, score, manage)

    # ------- ATR / Pools -------
    try:
        atr_val = float(compute_atr(df).iloc[-1])
    except Exception:
        # fallback ATR (volatilité % * prix * facteur)
        if len(df) > 25:
            atr_val = float(pd.Series(df["close"]).pct_change().rolling(20).std().iloc[-1] * df["close"].iloc[-1] * 1.5)
        else:
            atr_val = 0.0

    try:
        pool_hi, pool_lo = equal_highs_lows(df, lookback=120, precision=2)
        pool_hi = bool(pool_hi); pool_lo = bool(pool_lo)
    except Exception:
        pool_hi = pool_lo = False

    tolerated: List[str] = []

    # ------- OTE & Momentum tolérances -------
    try:
        in_ote = bool(indi.is_price_in_ote_zone(df, getattr(setup, "side", "LONG")))
    except Exception:
        in_ote = True
    if not in_ote:
        tolerated.append("OTE")

    try:
        diverge_ok = bool(indi.is_momentum_ok(df))
    except Exception:
        diverge_ok = True
    if not diverge_ok:
        tolerated.append("DIVERGENCE")

    for n in struct_notes:
        if n not in tolerated:
            tolerated.append(n)

    # ------- SL/TP/RR -------
    side = getattr(setup, "side", "LONG")
    mult_atr = float(getattr(SETTINGS, "sl_atr_mult", 1.5))
    tp1_rr = float(getattr(SETTINGS, "tp1_rr", 1.0))
    tp2_rr = float(getattr(SETTINGS, "tp2_rr", 2.0))

    if side == "LONG":
        pool_guard = df["low"].tail(120).min() if pool_lo else entry_price - mult_atr * atr_val
        sl = min(pool_guard, entry_price - mult_atr * atr_val)
        risk = max(1e-9, entry_price - sl)
        tp1 = entry_price + tp1_rr * risk
        tp2 = entry_price + tp2_rr * risk
    else:
        pool_guard = df["high"].tail(120).max() if pool_hi else entry_price + mult_atr * atr_val
        sl = max(pool_guard, entry_price + mult_atr * atr_val)
        risk = max(1e-9, sl - entry_price)
        tp1 = entry_price - tp1_rr * risk
        tp2 = entry_price - tp2_rr * risk

    rr = abs((tp1 - entry_price) / risk) if risk > 0 else 0.0
    req_rr_min = float(getattr(SETTINGS, "req_rr_min", 1.2))
    allow_tol_rr = bool(getattr(SETTINGS, "allow_tol_rr", True))

    if rr < req_rr_min and not allow_tol_rr:
        reason = (
            f"REJET — RR {rr:.2f} < req {req_rr_min:.2f}\n"
            f"Institutionnel: OI={oi_s:.2f} Δ={dlt_s:.2f} fund={fund_s:.2f} liq[{liq_src}]={liq_s:.2f} "
            + (f"book={book_s:.2f}" if inst_diag['thresholds']['use_book'] else "book=NA") + "\n"
            f"Technique: ema_trend={tech_diag['ema_trend']} macd={tech_diag['macd']}\n"
            f"Structure: bos={struct_diag['bos']} choch={struct_diag['choch']} engulf={struct_diag['engulf']}\n"
            f"Macro: enabled={macro is not None and getattr(SETTINGS, 'use_macro', False)}\n"
            f"Tolérées: {', '.join(sorted(set(tolerated))) if tolerated else 'Aucune'}"
        )
        manage = {"diagnostics": _build_diagnostics(inst_diag, tech_diag, struct_diag, macro_diag, tolerated, ["rr_below_min"])}
        return Decision("NONE", getattr(setup, "name", "None"), reason, [],
                        float(rr), float(entry_price), float(sl), float(tp1), float(tp2), float(score), manage)

    if rr < req_rr_min and allow_tol_rr:
        if "RR" not in tolerated:
            tolerated.append("RR")

    if not tech_ok and "DIVERGENCE" not in tolerated:
        tolerated.append("DIVERGENCE")
    if not struct_ok and "COS" not in tolerated:
        tolerated.append("COS")

    # ------- Raison détaillée -------
    reason = (
        f"ACCEPTÉ — {side} | Score={score:.2f}\n"
        f"Institutionnel: OI={oi_s:.2f} Δ={dlt_s:.2f} fund={fund_s:.2f} liq[{liq_src}]={liq_s:.2f} "
        + (f"book={book_s:.2f}" if inst_diag['thresholds']['use_book'] else "book=NA") + "\n"
        f"ComposantesOK: {inst_diag['components_ok_count']}/{inst_diag['thresholds']['components_min']}  "
        f"Details={inst_diag['components_ok']}\n"
        f"Technique: ema_trend={tech_diag['ema_trend']} macd={tech_diag['macd']}\n"
        f"Structure: bos={struct_diag['bos']} choch={struct_diag['choch']} engulf={struct_diag['engulf']}\n"
        f"Macro: enabled={getattr(SETTINGS, 'use_macro', False)}\n"
        f"RR={rr:.2f} (tp1@{tp1:.6f} tp2@{tp2:.6f} sl@{sl:.6f})\n"
        f"Tolérées: {', '.join(sorted(set(tolerated))) if tolerated else 'Aucune'}"
    )

    manage = {
        "tp1_part": float(getattr(SETTINGS, "tp1_part", 0.5)),
        "move_to_be_after_tp1": bool(getattr(SETTINGS, "breakeven_after_tp1", True)),
        "trail_after_tp1_mult_atr": float(getattr(SETTINGS, "trail_mult_atr", 1.0)),
        # Diagnostics pour logger côté scanner sans dépendre du format interne
        "diagnostics": _build_diagnostics(inst_diag, tech_diag, struct_diag, macro_diag, tolerated, []),
    }

    return Decision(
        side=getattr(setup, "side", "LONG"),
        name=getattr(setup, "name", "setup"),
        reason=reason,
        tolerated=sorted(set(tolerated)),
        rr=float(rr),
        entry=float(entry_price),
        sl=float(sl),
        tp1=float(tp1),
        tp2=float(tp2),
        score=float(score),
        manage=manage,
    )
