from dataclasses import dataclass
from typing import Literal, Dict, Any, Tuple
import pandas as pd

from config import SETTINGS
from orderflow_features import compute_atr, equal_highs_lows
from strategy_setups import initiative_breakout, vwap_reversion, stoprun_reversal

# Placeholders pour tes modules existants (si non fournis on garde des checks simples)
try:
    import indicators as indi
except Exception:
    class _DummyIndi:
        def macd(self, s):
            ema12 = s.ewm(span=12, adjust=False).mean()
            ema26 = s.ewm(span=26, adjust=False).mean()
            macd  = ema12 - ema26
            signal = macd.ewm(span=9, adjust=False).mean()
            return macd, signal
        def is_price_in_ote_zone(self, df, side): return True
        def is_momentum_ok(self, df): return True
    indi = _DummyIndi()

try:
    import structure_utils as su
except Exception:
    class _DummySU:
        def has_recent_bos(self, df): return True
        def is_choch_conditioned(self, df): return True
        def is_bullish_engulfing(self, df): return False
        def is_bearish_engulfing(self, df): return False
    su = _DummySU()

@dataclass
class Decision:
    side: Literal["LONG","SHORT","NONE"]
    name: str
    reason: str
    tolerated: list
    rr: float
    entry: float
    sl: float
    tp1: float
    tp2: float
    score: float
    manage: dict


# ------------------------- Helpers -------------------------

def _tech_context_ok(df: pd.DataFrame) -> Tuple[bool, Dict[str, bool]]:
    """Contexte technique simple: tendance & momentum."""
    e20 = df["close"].ewm(span=20, adjust=False).mean().iloc[-1]
    e50 = df["close"].ewm(span=50, adjust=False).mean().iloc[-1]
    macd, signal = indi.macd(df["close"])
    macd_ok  = bool(macd.iloc[-1] > signal.iloc[-1])
    trend_ok = bool(e20 > e50)
    return bool(trend_ok and macd_ok), {"ema_trend": trend_ok, "macd": macd_ok}


def _macro_filter_ok(macro: Dict[str, float]) -> Tuple[bool, Dict[str, float | bool]]:
    """Filtre macro: stress si drawdown fort sur TOTAL/TOTAL2 ou dominance BTC élevée."""
    if not getattr(SETTINGS, "use_macro", False):
        return True, {"enabled": False}
    total = float(macro.get("TOTAL", 0.0) or 0.0)
    total2 = float(macro.get("TOTAL2", 0.0) or 0.0)
    dom   = float(macro.get("BTC_DOM", 0.0) or 0.0)
    tpct  = float(macro.get("TOTAL_PCT", 0.0) or 0.0)
    t2pct = float(macro.get("TOTAL2_PCT", 0.0) or 0.0)

    stress_total  = (tpct  < -0.02)
    stress_total2 = (getattr(SETTINGS, "use_total2", False) and (t2pct < -0.03))
    # garde-fou si macro vide/buguée
    stress_dom    = (dom > 0.58 and dom < 1.5)

    ok = not (stress_total or stress_total2 or stress_dom)
    return ok, {
        "enabled": True,
        "TOTAL_PCT": tpct,
        "TOTAL2_PCT": t2pct,
        "BTC_DOM": dom,
        "stress_total": stress_total,
        "stress_total2": stress_total2,
        "stress_dom": stress_dom
    }


def _structure_filter_ok(df: pd.DataFrame) -> Tuple[bool, list, Dict[str, bool]]:
    """Structure de marché: BOS/CHoCH + bougie d’activation."""
    notes = []
    try:
        bos_ok   = bool(su.has_recent_bos(df))
        choch_ok = bool(su.is_choch_conditioned(df))
        engulf   = bool(su.is_bullish_engulfing(df) or su.is_bearish_engulfing(df))
    except Exception:
        bos_ok = choch_ok = True
        engulf = True
    if not (bos_ok or choch_ok): notes.append("COS")
    if not engulf: notes.append("BOUGIE")

    return ((bos_ok or choch_ok) and engulf), notes, {
        "bos": bos_ok, "choch": choch_ok, "engulf": engulf
    }


def _pick_setup(entry_price: float, inst: dict, df: pd.DataFrame):
    """Essaye plusieurs setups et prend le premier valide."""
    cands = [
        initiative_breakout(entry_price, inst, df),
        vwap_reversion(entry_price, inst, df, vwap_col="vwap_US"),
        stoprun_reversal(entry_price, inst, df),
    ]
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

    # Récupération des sous-scores
    oi_s    = float(inst.get("oi_score", 0.0) or 0.0)
    dlt_s   = float(inst.get("delta_score", 0.0) or 0.0)
    fund_s  = float(inst.get("funding_score", 0.0) or 0.0)
    # Nouveau calcul liquidations prioritaire si présent
    liq_new = inst.get("liq_new_score", None)
    liq_s   = float(liq_new if liq_new is not None else (inst.get("liq_score", 0.0) or 0.0))
    book_s  = float(inst.get("book_imbal_score", 0.0) or 0.0)

    # Seuils (avec défauts sûrs)
    req_score_min       = float(getattr(SETTINGS, "req_score_min", 1.5))
    oi_min              = float(getattr(SETTINGS, "oi_req_min", 0.4))
    delta_min           = float(getattr(SETTINGS, "delta_req_min", 0.4))
    funding_min         = float(getattr(SETTINGS, "funding_req_min", 0.2))
    liq_min             = float(getattr(SETTINGS, "liq_req_min", 0.5))  # liquidations un peu plus exigeant
    book_min            = float(getattr(SETTINGS, "book_req_min", 0.3))
    use_book            = bool(getattr(SETTINGS, "use_book_imbal", True))
    inst_components_min = int(getattr(SETTINGS, "inst_components_min", 2))

    # Évaluation composantes
    comp_status = {
        "oi_ok":     oi_s    >= oi_min,
        "delta_ok":  dlt_s   >= delta_min,
        "fund_ok":   fund_s  >= funding_min,
        "liq_ok":    liq_s   >= liq_min,
        "book_ok":   (book_s >= book_min) if use_book else None,  # None = non utilisé
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


# ------------------------- Main -------------------------

def analyze_signal(entry_price: float, df: pd.DataFrame, inst: Dict[str, Any], macro: Dict[str, float] | None = None) -> Decision:
    # ------- Institutionnel : score global + porte composantes (incl. liquidations new) -------
    inst_ok, inst_diag = _institutional_gate(inst)
    score   = float(inst_diag["score"])
    oi_s    = float(inst_diag["oi_score"])
    dlt_s   = float(inst_diag["delta_score"])
    fund_s  = float(inst_diag["funding_score"])
    liq_s   = float(inst_diag["liq_score"])
    book_s  = float(inst_diag["book_score"])
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
        return Decision("NONE","None",reason,[],0.0,entry_price,0.0,0.0,0.0,score,{})

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
        return Decision("NONE","None",reason,[],0.0,entry_price,0.0,0.0,0.0,score,{})

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
        return Decision("NONE", getattr(setup, "name", "None"), reason,
                        [], 0.0, entry_price, 0.0, 0.0, 0.0, score, {})

    # ------- ATR / Pools -------
    try:
        atr = float(compute_atr(df).iloc[-1])
    except Exception:
        # fallback ATR simple si souci data
        atr = float(pd.Series(df["close"]).pct_change().rolling(20).std().iloc[-1] * df["close"].iloc[-1] * 1.5) if len(df) > 25 else 0.0

    pool_hi, pool_lo = equal_highs_lows(df, lookback=120, precision=2)
    tolerated: list[str] = []

    # ------- OTE & Momentum tolérances -------
    try:
        in_ote = bool(indi.is_price_in_ote_zone(df, getattr(setup, "side", "LONG")))
    except Exception:
        in_ote = True
    if not in_ote: tolerated.append("OTE")

    try:
        diverge_ok = bool(indi.is_momentum_ok(df))
    except Exception:
        diverge_ok = True
    if not diverge_ok and "DIVERGENCE" not in tolerated:
        tolerated.append("DIVERGENCE")

    for n in struct_notes:
        if n not in tolerated:
            tolerated.append(n)

    # ------- SL/TP/RR -------
    side = getattr(setup, "side", "LONG")
    if side == "LONG":
        pool_guard = df["low"].tail(120).min() if pool_lo else entry_price - getattr(SETTINGS, "sl_atr_mult", 1.5) * atr
        sl  = min(pool_guard, entry_price - getattr(SETTINGS, "sl_atr_mult", 1.5) * atr)
        risk = max(1e-9, entry_price - sl)
        tp1 = entry_price + getattr(SETTINGS, "tp1_rr", 1.0) * risk
        tp2 = entry_price + getattr(SETTINGS, "tp2_rr", 2.0) * risk
    else:
        pool_guard = df["high"].tail(120).max() if pool_hi else entry_price + getattr(SETTINGS, "sl_atr_mult", 1.5) * atr
        sl  = max(pool_guard, entry_price + getattr(SETTINGS, "sl_atr_mult", 1.5) * atr)
        risk = max(1e-9, sl - entry_price)
        tp1 = entry_price - getattr(SETTINGS, "tp1_rr", 1.0) * risk
        tp2 = entry_price - getattr(SETTINGS, "tp2_rr", 2.0) * risk

    rr = abs((tp1 - entry_price) / risk) if risk > 0 else 0.0
    req_rr_min = float(getattr(SETTINGS, "req_rr_min", 1.2))
    allow_tol_rr = bool(getattr(SETTINGS, "allow_tol_rr", True))

    if rr < req_rr_min:
        if allow_tol_rr:
            if "RR" not in tolerated: tolerated.append("RR")
        else:
            reason = (
                f"REJET — RR {rr:.2f} < req {req_rr_min:.2f}\n"
                f"Institutionnel: OI={oi_s:.2f} Δ={dlt_s:.2f} fund={fund_s:.2f} liq[{liq_src}]={liq_s:.2f} "
                + (f"book={book_s:.2f}" if inst_diag['thresholds']['use_book'] else "book=NA") + "\n"
                f"Technique: ema_trend={tech_diag['ema_trend']} macd={tech_diag['macd']}\n"
                f"Structure: bos={struct_diag['bos']} choch={struct_diag['choch']} engulf={struct_diag['engulf']}\n"
                f"Macro: enabled={macro is not None and getattr(SETTINGS, 'use_macro', False)}\n"
                f"Tolérées: {', '.join(tolerated) if tolerated else 'Aucune'}"
            )
            return Decision("NONE", getattr(setup, "name", "None"), reason, [], float(rr),
                            float(entry_price), float(sl), float(tp1), float(tp2), float(score), {})

    # Si technique/structure KO, on marque comme tolérance (sans bloquer si le reste est fort)
    if not tech_ok and "DIVERGENCE" not in tolerated:
        tolerated.append("DIVERGENCE")
    if not struct_ok and "COS" not in tolerated:
        tolerated.append("COS")

    # ------- Raison détaillée (multi-lignes pour logs Railway) -------
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
        f"Tolérées: {', '.join(tolerated) if tolerated else 'Aucune'}"
    )

    manage = {
        "tp1_part": float(getattr(SETTINGS, "tp1_part", 0.5)),
        "move_to_be_after_tp1": bool(getattr(SETTINGS, "breakeven_after_tp1", True)),
        "trail_after_tp1_mult_atr": float(getattr(SETTINGS, "trail_mult_atr", 1.0)),
    }

    return Decision(side, getattr(setup, "name", "setup"), reason, tolerated, float(rr),
                    float(entry_price), float(sl), float(tp1), float(tp2),
                    float(score), manage)
