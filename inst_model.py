# inst_model.py
from __future__ import annotations
import os, json, time, logging, statistics
from typing import Dict, Any, Tuple
import numpy as np
from inst_sources import funding_rates, open_interest_hist, long_short_ratio, klines
from inst_features import cvd_from_klines, oi_delta_strength, funding_score, liq_stress

log = logging.getLogger("inst.model")
STATE_PATH = os.getenv("INST_STATE_PATH", "inst_state.json")
WINDOW = int(os.getenv("INST_WINDOW", "240"))
FLOOR_REQ = float(os.getenv("INST_REQ_FLOOR", "1.2"))
def FLO0(symbol: str) -> float:
    s = symbol.upper()
    return 1.4 if s.startswith("BTC") or s.startswith("ETH") else FLOOR_REQ

class InstAutoTune:
    def __init__(self):
        self.state = self._load()
    def _load(self) -> Dict[str, Any]:
        if not os.path.exists(STATE_PATH): return {}
        try: return json.load(open(STATE_PATH, "r", encoding="utf-8"))
        except Exception: return {}
    def _save(self): 
        try: json.dump(self.state, open(STATE_PATH, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
        except Exception as e: log.warning("save inst state KO: %s", e)
    def add(self, symbol: str, score: float, cvd: float):
        s = self.state.setdefault(symbol, {"scores": [], "cvd": []})
        s["scores"].append(float(score)); s["cvd"].append(float(cvd))
        if len(s["scores"]) > WINDOW: s["scores"] = s["scores"][-WINDOW:]
        if len(s["cvd"])    > WINDOW: s["cvd"]    = s["cvd"][-WINDOW:]
        self.state[symbol] = s; self._save()
    def thresholds(self, symbol: str) -> Tuple[float, float]:
        s = self.state.get(symbol, {})
        arr = [float(x) for x in s.get("scores", [])]
        if not arr:
            return FLO0(symbol), 0.0
        arr = sorted(arr)
        k = int(np.ceil(0.70 * len(arr)) - 1)
        k = max(0, min(len(arr)-1, k))
        req = max(arr[k], FLO0(symbol))
        cvd_arr = [float(x) for x in s.get("cvd", [])]
        cvd_mean = float(statistics.mean(cvd_arr)) if cvd_arr else 0.0
        return req, cvd_mean

def infer_side_from_cvd(cvd: float, cvd_base: float) -> str:
    if cvd >= cvd_base: return "long"
    return "short"

def compute_institutional(symbol: str) -> Dict[str, Any]:
    fr   = funding_rates(symbol, limit=24)
    oi   = open_interest_hist(symbol, period="5m", limit=60)
    lsr  = long_short_ratio(symbol, period="5m", limit=60)
    kl   = klines(symbol, interval="5m", limit=200)
    cvd  = cvd_from_klines(kl)
    oi_s = oi_delta_strength(oi)
    fr_s = funding_score(fr)
    liq_s= liq_stress(lsr)
    raw_score = 0.45*max(0.0, oi_s) + 0.25*max(0.0, fr_s) + 0.20*max(0.0, liq_s) + 0.10*abs(cvd)/max(abs(cvd),1.0)
    return { "fr": fr, "oi": oi, "lsr": lsr, "cvd": cvd, "oi_s": float(oi_s), "fr_s": float(fr_s), "liq_s": float(liq_s), "score_raw": float(raw_score) }

def decide_institutional(symbol: str, tuner: InstAutoTune) -> Dict[str, Any]:
    snap = compute_institutional(symbol)
    req, cvd_base = tuner.thresholds(symbol)
    raw = float(snap["score_raw"])
    score_final = 1.0 + max(0.0, raw - req)  # lisible et auto-adapté
    side_bias = infer_side_from_cvd(float(snap["cvd"]), float(cvd_base))
    tuner.add(symbol, score_final, float(snap["cvd"]))
    return { "score": float(score_final), "req": float(req), "side_bias": side_bias, **snap }
