# backtest_h1.py — harness minimal
from __future__ import annotations
import pandas as pd
from analyze_signal import evaluate_signal

def run_backtest(df: pd.DataFrame, symbol:str="TESTUSDTM", tick:float=0.01):
    wins=loss=0
    for i in range(120, len(df)-1):
        wnd = df.iloc[:i].copy()
        entry = float(wnd["close"].iloc[-1])
        signal = {"symbol":symbol, "bias":"LONG", "df":wnd, "entry":entry, "tick":tick, "ote":True}
        res = evaluate_signal(signal)
        if not res.get("valid"): continue
        # ultra simple PNL: TP1 touché avant SL? (à améliorer)
        sl = res.get("sl"); tp1 = res.get("tp1")
        nextbar = df.iloc[i+1]
        touched_tp = nextbar["high"] >= tp1
        touched_sl = nextbar["low"]  <= sl
        if touched_tp and not touched_sl: wins+=1
        elif touched_sl and not touched_tp: loss+=1
    return wins, loss
