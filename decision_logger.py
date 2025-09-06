# decision_logger.py — logging structuré par symbole (tech + insti + décision)
from __future__ import annotations
import json, logging
from typing import Any, Dict, Iterable, Optional

LOG = logging.getLogger("runner")

def _fmt_bool(v: Optional[bool]) -> str:
    if v is True:  return "✓"
    if v is False: return "✗"
    return "-"

def _kv_line(items: Iterable[tuple[str, Any]]) -> str:
    parts = []
    for k, v in items:
        if isinstance(v, float):
            parts.append(f"{k}={v:.2f}")
        elif isinstance(v, (dict, list, tuple)):
            try:
                parts.append(f"{k}={json.dumps(v, ensure_ascii=False)}")
            except Exception:
                parts.append(f"{k}={v}")
        else:
            parts.append(f"{k}={v}")
    return " ".join(parts)

def log_institutional(symbol: str, score: float, req: float, comps_ok: int, comps_req: int,
                      details: Dict[str, Optional[bool]] | None = None,
                      extras: Dict[str, Any] | None = None) -> None:
    """Log institutionnel: score + détails (oi_ok, delta_ok, funding_ok, liq_ok, book_ok)."""
    base = [("s", score), ("req", req), ("comps", f"{comps_ok}/{comps_req}")]
    if extras:
        for k, v in extras.items():
            base.append((k, v))
    LOG.info("[%s] inst-gate: %s", symbol, _kv_line(base))
    if details:
        det = {k: _fmt_bool(details.get(k)) for k in ("oi_ok","delta_ok","fund_ok","liq_ok","book_ok")}
        LOG.info("[%s] inst-details: %s", symbol, _kv_line(det.items()))

def log_tech(symbol: str, tech_flags: Dict[str, Optional[bool]], tolerated: Iterable[str] | None = None) -> None:
    """Log techniques: chaque condition clé, + liste de tolérances appliquées."""
    order = [
        "ema_trend_ok", "momentum_ok", "bos_strength_ok", "cos_ok",
        "divergence_ok", "fvg_ok", "liquidity_zone_ok", "ote_ok", "rr_ok"
    ]
    shown = []
    for k in order:
        if k in tech_flags:
            shown.append((k.replace("_ok",""), _fmt_bool(tech_flags.get(k))))
    # Print any extra flags not in the default order
    for k,v in tech_flags.items():
        if k not in [x for x,_ in shown]:
            shown.append((k, _fmt_bool(v)))
    LOG.info("[%s] tech: %s", symbol, _kv_line(shown))
    if tolerated:
        LOG.info("[%s] tech-tolerated: %s", symbol, ", ".join(sorted(tolerated)))

def log_macro(symbol: str, macro: Dict[str, Any] | None) -> None:
    if not macro: return
    keep = {}
    for k in ("TOTAL","TOTAL_PCT","TOTAL2","TOTAL2_PCT","BTC_DOM","BTC_KEYLEVEL"):
        if k in macro: keep[k] = macro[k]
    if keep:
        LOG.info("[%s] macro: %s", symbol, _kv_line(keep.items()))

def log_decision(symbol: str, accepted: bool, reason_blocks: Iterable[str] | None,
                 rr_gross: Optional[float] = None, rr_net: Optional[float] = None,
                 side: Optional[str] = None, entry: Optional[float] = None,
                 sl: Optional[float] = None, tp1: Optional[float] = None, tp2: Optional[float] = None,
                 score: Optional[float] = None) -> None:
    state = "ACCEPT" if accepted else "REJECT"
    base = [("state", state)]
    if side is not None:  base.append(("side", side))
    if score is not None: base.append(("score", score))
    if rr_gross is not None: base.append(("rr", rr_gross))
    if rr_net  is not None: base.append(("rr_net", rr_net))
    if entry is not None:   base.append(("entry", entry))
    if sl is not None:      base.append(("sl", sl))
    if tp1 is not None:     base.append(("tp1", tp1))
    if tp2 is not None:     base.append(("tp2", tp2))
    LOG.info("[%s] decision: %s", symbol, _kv_line(base))
    if not accepted and reason_blocks:
        LOG.info("[%s] reject-reasons: %s", symbol, ", ".join(reason_blocks))

def extract_debug_from_locals(loc: Dict[str, Any]) -> Dict[str, Any]:
    """Optionnel: depuis analyze_signal, construit un dict diag sans casser l'existant."""
    def _g(*names, default=None):
        for n in names:
            if n in loc: return loc[n]
        return default
    tech = {
        "ema_trend_ok":      _g("ema_trend_ok", default=None),
        "momentum_ok":       _g("momentum_ok",  default=None),
        "bos_strength_ok":   _g("bos_strength_ok", "bos_ok", default=None),
        "cos_ok":            _g("cos_ok", default=None),
        "divergence_ok":     _g("divergence_ok", default=None),
        "fvg_ok":            _g("fvg_ok", default=None),
        "liquidity_zone_ok": _g("liquidity_zone_ok","has_liquidity_zone", default=None),
        "ote_ok":            _g("ote_ok", default=None),
        "rr_ok":             _g("rr_ok", default=None),
    }
    inst = {
        "score":     _g("inst_score","institutional_score", default=None),
        "oi_ok":     _g("oi_ok", default=None),
        "delta_ok":  _g("delta_ok","cvd_ok", default=None),
        "fund_ok":   _g("fund_ok","funding_ok", default=None),
        "liq_ok":    _g("liq_ok","liquidations_ok", default=None),
        "book_ok":   _g("book_ok","orderbook_ok", default=None),
        "comps_ok":  _g("inst_components_ok","components_ok", default=None),
        "comps_req": _g("inst_components_min","components_min","inst_components_min", default=None),
    }
    out = {"tech": tech, "inst": inst}
    try:
        out["tolerated"] = list(_g("tolerated_set","tolerances","tol_set", default=[]) or [])
    except Exception:
        out["tolerated"] = []
    try:
        out["reasons_block"] = list(_g("reasons_block","reject_reasons","rejects", default=[]) or [])
    except Exception:
        out["reasons_block"] = []
    return out
