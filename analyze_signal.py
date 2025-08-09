from dataclasses import dataclass
from typing import Literal, Dict, Any
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

def _tech_context_ok(df: pd.DataFrame) -> tuple[bool, Dict[str, bool]]:
    """Contexte technique simple: tendance & momentum."""
    e20 = df["close"].ewm(span=20, adjust=False).mean().iloc[-1]
    e50 = df["close"].ewm(span=50, adjust=False).mean().iloc[-1]
    macd, signal = indi.macd(df["close"])
    macd_ok  = bool(macd.iloc[-1] > signal.iloc[-1])
    trend_ok = bool(e20 > e50)
    return bool(trend_ok and macd_ok), {"ema_trend": trend_ok, "macd": macd_ok}

def _macro_filter_ok(macro: Dict[str, float]) -> tuple[bool, Dict[str, float|bool]]:
    """Filtre macro: stress si drawdown fort sur TOTAL/TOTAL2 ou dominance BTC élevée."""
    if not SETTINGS.use_macro:
        return True, {"enabled": False}
    total = float(macro.get("TOTAL", 0.0) or 0.0)
    total2 = float(macro.get("TOTAL2", 0.0) or 0.0)
    dom   = float(macro.get("BTC_DOM", 0.0) or 0.0)
    tpct  = float(macro.get("TOTAL_PCT", 0.0) or 0.0)
    t2pct = float(macro.get("TOTAL2_PCT", 0.0) or 0.0)

    stress_total  = (tpct  < -0.02)
    stress_total2 = (SETTINGS.use_total2 and (t2pct < -0.03))
    stress_dom    = (dom > 0.58 and dom < 1.5)  # garde-fou si macro vide

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

def _structure_filter_ok(df: pd.DataFrame) -> tuple[bool, list, Dict[str, bool]]:
    """Structure de marché: BOS/COS + bougie d’activation."""
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
        stoprun_reversal(entry_price, inst, df)
    ]
    for c in cands:
        if getattr(c, "side", "NONE") != "NONE":
            return c
    return cands[0]

def analyze_signal(entry_price: float, df: pd.DataFrame, inst: Dict[str,Any], macro: Dict[str,float] | None = None) -> Decision:
    # ------- Institutionnel (score global déjà pondéré ailleurs) -------
    score = float(inst.get("score", 0.0) or 0.0)

    oi_s   = float(inst.get("oi_score", 0.0) or 0.0)
    dlt_s  = float(inst.get("delta_score", 0.0) or 0.0)
    fund_s = float(inst.get("funding_score", 0.0) or 0.0)
    liq_s  = float(inst.get("liq_score", 0.0) or 0.0)
    book_s = float(inst.get("book_imbal_score", 0.0) or 0.0)

    if score < SETTINGS.req_score_min:
        reason = (
            f"REJET — Score institutionnel insuffisant "
            f"(score={score:.2f} < req={SETTINGS.req_score_min:.2f})\n"
            f"Institutionnel: OI={oi_s:.2f} Δ={dlt_s:.2f} fund={fund_s:.2f} liq={liq_s:.2f} book={book_s:.2f}"
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
            f"Institutionnel: OI={oi_s:.2f} Δ={dlt_s:.2f} fund={fund_s:.2f} liq={liq_s:.2f} book={book_s:.2f}\n"
            f"Technique: ema_trend={tech_diag['ema_trend']} macd={tech_diag['macd']}\n"
            f"Structure: bos={struct_diag['bos']} choch={struct_diag['choch']} engulf={struct_diag['engulf']}"
        )
        return Decision("NONE", getattr(setup, "name", "None"), getattr(setup, "reason", "setup invalide"),
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
    if not diverge_ok: tolerated.append("DIVERGENCE")

    for n in struct_notes:
        if n not in tolerated:
            tolerated.append(n)

    # ------- SL/TP/RR -------
    side = getattr(setup, "side", "LONG")
    if side == "LONG":
        pool_guard = df["low"].tail(120).min() if pool_lo else entry_price - SETTINGS.sl_atr_mult * atr
        sl  = min(pool_guard, entry_price - SETTINGS.sl_atr_mult * atr)
        risk = max(1e-9, entry_price - sl)
        tp1 = entry_price + SETTINGS.tp1_rr * risk
        tp2 = entry_price + SETTINGS.tp2_rr * risk
    else:
        pool_guard = df["high"].tail(120).max() if pool_hi else entry_price + SETTINGS.sl_atr_mult * atr
        sl  = max(pool_guard, entry_price + SETTINGS.sl_atr_mult * atr)
        risk = max(1e-9, sl - entry_price)
        tp1 = entry_price - SETTINGS.tp1_rr * risk
        tp2 = entry_price - SETTINGS.tp2_rr * risk

    rr = abs((tp1 - entry_price) / risk) if risk > 0 else 0.0
    if rr < SETTINGS.req_rr_min:
        if SETTINGS.allow_tol_rr:
            if "RR" not in tolerated: tolerated.append("RR")
        else:
            reason = (
                f"REJET — RR {rr:.2f} < req {SETTINGS.req_rr_min:.2f}\n"
                f"Institutionnel: OI={oi_s:.2f} Δ={dlt_s:.2f} fund={fund_s:.2f} liq={liq_s:.2f} book={book_s:.2f}\n"
                f"Technique: ema_trend={tech_diag['ema_trend']} macd={tech_diag['macd']}\n"
                f"Structure: bos={struct_diag['bos']} choch={struct_diag['choch']} engulf={struct_diag['engulf']}\n"
                f"Macro: enabled={macro is not None and SETTINGS.use_macro}\n"
                f"Tolérées: {', '.join(tolerated) if tolerated else 'Aucune'}"
            )
            return Decision("NONE", getattr(setup, "name", "None"), reason, [], float(rr),
                            float(entry_price), float(sl), float(tp1), float(tp2), float(score), {})

    # Si technique/structure KO, on marque comme tolérance (sans bloquer dur si le reste est fort)
    if not tech_ok and "DIVERGENCE" not in tolerated:
        tolerated.append("DIVERGENCE")
    if not struct_ok and "COS" not in tolerated:
        tolerated.append("COS")

    # ------- Raison détaillée (multi-lignes pour logs Railway) -------
    reason = (
        f"ACCEPTÉ — {side} | Score={score:.2f}\n"
        f"Institutionnel: OI={oi_s:.2f} Δ={dlt_s:.2f} fund={fund_s:.2f} liq={liq_s:.2f} book={book_s:.2f}\n"
        f"Technique: ema_trend={tech_diag['ema_trend']} macd={tech_diag['macd']}\n"
        f"Structure: bos={struct_diag['bos']} choch={struct_diag['choch']} engulf={struct_diag['engulf']}\n"
        f"Macro: enabled={SETTINGS.use_macro}\n"
        f"RR={rr:.2f} (tp1@{tp1:.6f} tp2@{tp2:.6f} sl@{sl:.6f})\n"
        f"Tolérées: {', '.join(tolerated) if tolerated else 'Aucune'}"
    )

    manage = {
        "tp1_part": float(SETTINGS.tp1_part),
        "move_to_be_after_tp1": bool(SETTINGS.breakeven_after_tp1),
        "trail_after_tp1_mult_atr": float(SETTINGS.trail_mult_atr)
    }

    return Decision(side, getattr(setup, "name", "setup"), reason, tolerated, float(rr),
                    float(entry_price), float(sl), float(tp1), float(tp2),
                    float(score), manage)
