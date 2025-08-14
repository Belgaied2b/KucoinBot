from dataclasses import dataclass
from typing import Literal
import pandas as pd
from orderflow_features import equal_highs_lows

@dataclass
class SetupDecision:
    side: Literal["LONG","SHORT","NONE"]
    name: str
    reason: str

def initiative_breakout(price: float, inst: dict, df: pd.DataFrame) -> SetupDecision:
    m20=df["close"].rolling(20).mean().iloc[-1]
    oi,delta,swe=inst["oi_score"], inst["delta_score"], inst.get("sweep_side","NONE")
    if oi>0.25 and delta>0.25 and price>m20 and swe in ("ASK","NONE"):
        return SetupDecision("LONG","InitiativeBreakout",f"OI {oi:.2f} Δ {delta:.2f} sweep={swe}")
    if oi>0.25 and delta>0.25 and price<m20 and swe in ("BID","NONE"):
        return SetupDecision("SHORT","InitiativeBreakout",f"OI {oi:.2f} Δ {delta:.2f} sweep={swe}")
    return SetupDecision("NONE","InitiativeBreakout","Pas de poussée initiative")

def vwap_reversion(price: float, inst: dict, df: pd.DataFrame, vwap_col="vwap_US") -> SetupDecision:
    if vwap_col not in df.columns: 
        return SetupDecision("NONE","VWAPReversion","VWAP indisponible")
    vwap=df[vwap_col].iloc[-1]
    spread=(price-vwap)/vwap if vwap else 0.0
    book=inst.get("book_imbal_score",0.0)
    if spread<-0.005 and book>0.2: return SetupDecision("LONG","VWAPReversion",f"Spread {spread:.3%} / book {book:.2f}")
    if spread> 0.005 and book>0.2: return SetupDecision("SHORT","VWAPReversion",f"Spread {spread:.3%} / book {book:.2f}")
    return SetupDecision("NONE","VWAPReversion","Pas d'écart exploitable")

def stoprun_reversal(price: float, inst: dict, df: pd.DataFrame) -> SetupDecision:
    pool_hi, pool_lo = equal_highs_lows(df, lookback=120, precision=2)
    liq=inst.get("liq_score",0.0)
    if pool_lo and liq>0.3: return SetupDecision("LONG","StopRunReversal",f"Pools bas + liq {liq:.2f}")
    if pool_hi and liq>0.3: return SetupDecision("SHORT","StopRunReversal",f"Pools haut + liq {liq:.2f}")
    return SetupDecision("NONE","StopRunReversal","Pas de stop-run net")
