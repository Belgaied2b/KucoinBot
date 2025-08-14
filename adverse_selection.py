from typing import Literal

def should_cancel_or_requote(side: Literal["LONG","SHORT"], inst: dict, cfg) -> str:
    book = float(inst.get("book_imbal_score",0.0))
    sweep = inst.get("sweep_side","NONE")
    funding = float(inst.get("funding",0.0))
    delta = float(inst.get("delta_score",0.0))

    if side=="LONG" and sweep=="BID":  # bids aspirés
        return "CANCEL_SWEEP"
    if side=="SHORT" and sweep=="ASK":
        return "CANCEL_SWEEP"

    if book > cfg.adverse_sweep_threshold:
        return "CANCEL_BOOK"

    if side=="LONG" and funding < -0.01 and delta < 0.15:
        return "CANCEL_FUNDELTA"
    if side=="SHORT" and funding > 0.01 and delta < 0.15:
        return "CANCEL_FUNDELTA"

    return "OK"
