
from __future__ import annotations
def _fee(amount: float, bps: float) -> float:
    return abs(amount) * (bps / 10000.0)
def _slippage(price: float, bps: float, side: str) -> float:
    shift = price * (bps / 10000.0)
    if side.lower() in ("buy","long"): return price + shift
    return price - shift
def rr_gross(entry: float, sl: float, tp: float, side: str) -> float:
    e, s, t = float(entry), float(sl), float(tp)
    if abs(e - s) < 1e-12: return 0.0
    if side.lower() in ("buy","long"): return abs(t - e) / abs(e - s)
    return abs(e - t) / abs(e - s)
def rr_net(entry: float, sl: float, tp: float, side: str, 
           maker_fee_bps: float = 2.0, taker_fee_bps: float = 5.0,
           maker_slip_bps: float = 0.5, taker_slip_bps: float = 2.0,
           fill_mode: str = "maker") -> float:
    side = side.lower(); fill_mode = (fill_mode or "maker").lower()
    e = float(entry); s = float(sl); t = float(tp)
    if fill_mode == "maker":
        e_eff = _slippage(e, maker_slip_bps, side); fee_in = _fee(e_eff, maker_fee_bps)
    else:
        e_eff = _slippage(e, taker_slip_bps, side); fee_in = _fee(e_eff, taker_fee_bps)
    t_eff = _slippage(t, taker_slip_bps, "sell" if side in ("buy","long") else "buy")
    s_eff = _slippage(s, taker_slip_bps, "sell" if side in ("buy","long") else "buy")
    fee_tp = _fee(t_eff, taker_fee_bps); fee_sl = _fee(s_eff, taker_fee_bps)
    if side in ("buy","long"):
        pnl_tp = (t_eff - e_eff) - (fee_in + fee_tp); pnl_sl = (s_eff - e_eff) - (fee_in + fee_sl)
    else:
        pnl_tp = (e_eff - t_eff) - (fee_in + fee_tp); pnl_sl = (e_eff - s_eff) - (fee_in + fee_sl)
    risk = abs(pnl_sl); return (pnl_tp / risk) if risk > 1e-12 else 0.0
def adjust_targets_for_costs(tp1: float, tp2: float, side: str, extra_bps: float = 3.0) -> tuple[float,float]:
    if side.lower() in ("buy","long"): return (tp1*(1 - extra_bps/10000.0), tp2*(1 - extra_bps/10000.0))
    return (tp1*(1 + extra_bps/10000.0), tp2*(1 + extra_bps/10000.0))
