# risk_guard.py
import os, json, time, logging
from typing import Dict, Any
log = logging.getLogger("risk")

STATE_PATH = os.getenv("RISK_STATE_PATH", "risk_state.json")
MAX_CONSEC_LOSSES = int(os.getenv("MAX_CONSEC_LOSSES", "3"))
COOLDOWN_MIN = int(os.getenv("COOLDOWN_MIN", "30"))

def _now() -> float: return time.time()
def _today() -> str: return time.strftime("%Y-%m-%d", time.gmtime())
def _load() -> Dict[str, Any]:
    if not os.path.exists(STATE_PATH): return {}
    try: return json.load(open(STATE_PATH,"r",encoding="utf-8"))
    except Exception: return {}
def _save(d: Dict[str, Any]): 
    try: json.dump(d, open(STATE_PATH,"w",encoding="utf-8"), ensure_ascii=False, indent=2)
    except Exception as e: log.warning("save risk_state KO: %s", e)

class RiskGuard:
    def __init__(self): self.state=_load()
    def _ensure_day(self):
        if self.state.get("day") != _today():
            self.state={"day": _today(), "losses":{}, "cd":{}}
            _save(self.state)
    def can_enter(self, symbol: str) -> (bool, str):
        self._ensure_day()
        cd = float(self.state.get("cd",{}).get(symbol,0))
        if cd > _now(): return False, f"cooldown {int(cd-_now())}s"
        return True, "ok"
    def notify_close(self, symbol: str, pnl_usdt: float):
        self._ensure_day()
        losses = int(self.state.get("losses",{}).get(symbol,0))
        if pnl_usdt < 0:
            losses += 1
            self.state.setdefault("losses",{})[symbol]=losses
            if losses >= MAX_CONSEC_LOSSES:
                self.state.setdefault("cd",{})[symbol] = _now() + COOLDOWN_MIN*60
        else:
            self.state.setdefault("losses",{})[symbol]=0
        _save(self.state)
