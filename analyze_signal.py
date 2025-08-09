from dataclasses import dataclass
from typing import Literal, Dict, Any
import pandas as pd

from config import SETTINGS
from orderflow_features import compute_atr, equal_highs_lows
from strategy_setups import initiative_breakout, vwap_reversion, stoprun_reversal

# Placeholders pour tes modules existants (si tu ne les fournis pas, on garde des checks simples)
try:
    import indicators as indi
except Exception:
    class _DummyIndi:
        def macd(self, s): import pandas as pd; ema12=s.ewm(span=12, adjust=False).mean(); ema26=s.ewm(span=26, adjust=False).mean(); macd=ema12-ema26; signal=macd.ewm(span=9, adjust=False).mean(); return macd, signal
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

def _tech_context_ok(df: pd.DataFrame) -> bool:
    e20 = df["close"].ewm(span=20, adjust=False).mean().iloc[-1]
    e50 = df["close"].ewm(span=50, adjust=False).mean().iloc[-1]
    macd, signal = indi.macd(df["close"])
    macd_ok = macd.iloc[-1] > signal.iloc[-1]
    trend_ok = e20 > e50
    return bool(trend_ok and macd_ok)

def _macro_filter_ok(macro: Dict[str,float]) -> bool:
    if not SETTINGS.use_macro: return True
    total = float(macro.get("TOTAL",0.0))
    total2 = float(macro.get("TOTAL2",0.0))
    dom = float(macro.get("BTC_DOM",0.0))
    if total<=0: return True
    stress = (macro.get("TOTAL_PCT",0.0) < -0.02) or (SETTINGS.use_total2 and macro.get("TOTAL2_PCT",0.0) < -0.03) or (dom > 0.58)
    return not stress

def _structure_filter_ok(df: pd.DataFrame):
    notes=[]
    try:
        bos_ok = su.has_recent_bos(df)
        choch_ok = su.is_choch_conditioned(df)
        engulf = su.is_bullish_engulfing(df) or su.is_bearish_engulfing(df)
    except Exception:
        bos_ok=choch_ok=True; engulf=True
    if not (bos_ok or choch_ok): notes.append("COS")
    if not engulf: notes.append("BOUGIE")
    return (bos_ok or choch_ok), notes

def _pick_setup(entry_price: float, inst: dict, df: pd.DataFrame):
    cands = [
        initiative_breakout(entry_price, inst, df),
        vwap_reversion(entry_price, inst, df, vwap_col="vwap_US"),
        stoprun_reversal(entry_price, inst, df)
    ]
    for c in cands:
        if c.side != "NONE":
            return c
    return cands[0]

def analyze_signal(entry_price: float, df: pd.DataFrame, inst: Dict[str,Any], macro: Dict[str,float]|None=None) -> Decision:
    score = float(inst.get("score",0.0))
    if score < SETTINGS.req_score_min:
        return Decision("NONE","None","Score institutionnel insuffisant",[],0,entry_price,0,0,0,score,{})

    macro_ok = _macro_filter_ok(macro or {})
    if not macro_ok:
        return Decision("NONE","None","Filtre macro défavorable (TOTAL/TOTAL2/Dominance)",[],0,entry_price,0,0,0,score,{})

    tech_ok = _tech_context_ok(df)
    struct_ok, struct_notes = _structure_filter_ok(df)

    setup = _pick_setup(entry_price, inst, df)
    if setup.side=="NONE":
        return Decision("NONE", setup.name, setup.reason, [], 0, entry_price,0,0,0, score, {})

    atr = compute_atr(df).iloc[-1]
    pool_hi, pool_lo = equal_highs_lows(df, lookback=120, precision=2)
    tolerated = []

    try:
        in_ote = indi.is_price_in_ote_zone(df, setup.side)
    except Exception:
        in_ote = True
    if not in_ote: tolerated.append("OTE")

    try:
        diverge_ok = indi.is_momentum_ok(df)
    except Exception:
        diverge_ok = True
    if not diverge_ok: tolerated.append("DIVERGENCE")

    for n in struct_notes:
        if n not in tolerated: tolerated.append(n)

    if setup.side=="LONG":
        pool_guard = df["low"].tail(120).min() if pool_lo else entry_price - SETTINGS.sl_atr_mult*atr
        sl = min(pool_guard, entry_price - SETTINGS.sl_atr_mult*atr)
        risk = entry_price - sl
        tp1 = entry_price + SETTINGS.tp1_rr * risk
        tp2 = entry_price + SETTINGS.tp2_rr * risk
    else:
        pool_guard = df["high"].tail(120).max() if pool_hi else entry_price + SETTINGS.sl_atr_mult*atr
        sl = max(pool_guard, entry_price + SETTINGS.sl_atr_mult*atr)
        risk = sl - entry_price
        tp1 = entry_price - SETTINGS.tp1_rr * risk
        tp2 = entry_price - SETTINGS.tp2_rr * risk

    rr = abs((tp1 - entry_price)/risk) if risk>0 else 0.0
    if rr < SETTINGS.req_rr_min:
        if SETTINGS.allow_tol_rr: tolerated.append("RR")
        else:
            return Decision("NONE", setup.name, f"RR {rr:.2f} < {SETTINGS.req_rr_min}", [], rr, entry_price, sl, tp1, tp2, score, {})

    if not tech_ok and "DIVERGENCE" not in tolerated: tolerated.append("DIVERGENCE")
    if not struct_ok and "COS" not in tolerated: tolerated.append("COS")

    reason = f"Score={score:.2f} | oi={inst.get('oi_score',0):.2f} Δ={inst.get('delta_score',0):.2f} fund={inst.get('funding_score',0):.2f} liq={inst.get('liq_score',0):.2f} book={inst.get('book_imbal_score',0):.2f}"
    manage = {
        "tp1_part": float(SETTINGS.tp1_part),
        "move_to_be_after_tp1": bool(SETTINGS.breakeven_after_tp1),
        "trail_after_tp1_mult_atr": float(SETTINGS.trail_mult_atr)
    }

    return Decision(setup.side, setup.name, reason, tolerated, float(rr),
                    float(entry_price), float(sl), float(tp1), float(tp2),
                    float(score), manage)
