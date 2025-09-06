# -*- coding: utf-8 -*-
"""
risk_guard.py — Garde-fous de niveau prop:
- Kill-switch (latence/stale/drawdown jour)
- Limites par symbole et pertes consécutives (cooldown)
- Exposition corrélée par cluster
"""

from __future__ import annotations
import os, json, time, logging
from typing import Dict, Any, Optional

STATE_PATH = os.environ.get("RISK_STATE_PATH", "risk_state.json")
DAILY_DD_LIMIT_PCT = float(os.environ.get("DAILY_DD_LIMIT_PCT", "3.0"))
MAX_CONSEC_LOSSES = int(os.environ.get("MAX_CONSEC_LOSSES", "3"))
COOLDOWN_MIN = int(os.environ.get("COOLDOWN_MIN", "30"))
MAX_LOSS_PER_SYMBOL_USDT = float(os.environ.get("MAX_LOSS_PER_SYMBOL_USDT", "50"))
CLUSTER_MAP = os.environ.get("CLUSTER_MAP", "")
CLUSTER_MAX_EXPOSURE = int(os.environ.get("CLUSTER_MAX_EXPOSURE", "3"))

def _now() -> float: return time.time()
def _today_key() -> str: return time.strftime("%Y-%m-%d", time.gmtime())

def _load_state() -> Dict[str, Any]:
    if not os.path.exists(STATE_PATH): return {}
    try:
        with open(STATE_PATH, "r", encoding="utf-8") as f: return json.load(f)
    except Exception: return {}

def _save_state(d: Dict[str, Any]) -> None:
    try:
        with open(STATE_PATH, "w", encoding="utf-8") as f: json.dump(d, f, ensure_ascii=False, indent=2)
    except Exception as e:
        logging.error("risk_guard: save_state KO: %s", e)

def cluster_of(symbol: str) -> str:
    if not CLUSTER_MAP:
        return "default"
    mapping = {}
    for part in CLUSTER_MAP.split(";"):
        if ":" in part:
            k, v = part.split(":", 1)
            mapping[k.strip().upper()] = v.strip().lower()
    return mapping.get(symbol.upper(), "default")

class RiskGuard:
    def __init__(self):
        self.state = _load_state()

    def _ensure_day(self):
        key = _today_key()
        if self.state.get("day") != key:
            self.state = {"day": key, "equity_start": None, "equity_low": None,
                          "consec_losses": {}, "cooldowns": {}, "cluster_open": {}}
            _save_state(self.state)

    def can_enter(self, symbol: str, ws_latency_ms: Optional[float], last_data_age_s: Optional[float]) -> (bool, str):
        self._ensure_day()
        if ws_latency_ms is not None and ws_latency_ms > 1200:
            return False, "latence WS élevée"
        if last_data_age_s is not None and last_data_age_s > 120:
            return False, "market data obsolètes"

        cd = self.state.get("cooldowns", {}).get(symbol, 0)
        if cd and cd > _now():
            return False, f"cooldown actif {int(cd - _now())}s"

        cl = cluster_of(symbol)
        open_cnt = int(self.state.get("cluster_open", {}).get(cl, 0))
        if open_cnt >= CLUSTER_MAX_EXPOSURE:
            return False, f"exposition cluster {cl} saturée"
        return True, "ok"

    def notify_open(self, symbol: str):
        self._ensure_day()
        cl = cluster_of(symbol)
        self.state.setdefault("cluster_open", {})
        self.state["cluster_open"][cl] = int(self.state["cluster_open"].get(cl, 0)) + 1
        _save_state(self.state)

    def notify_close(self, symbol: str, pnl_usdt: float):
        self._ensure_day()
        cl = cluster_of(symbol)
        self.state.setdefault("cluster_open", {})
        self.state["cluster_open"][cl] = max(0, int(self.state["cluster_open"].get(cl, 0)) - 1)
        cons = int(self.state.setdefault("consec_losses", {}).get(symbol, 0))
        if pnl_usdt < 0:
            cons += 1
            self.state["consec_losses"][symbol] = cons
            if cons >= MAX_CONSEC_LOSSES:
                until = _now() + COOLDOWN_MIN * 60
                self.state.setdefault("cooldowns", {})[symbol] = until
        else:
            self.state["consec_losses"][symbol] = 0
        if pnl_usdt < -MAX_LOSS_PER_SYMBOL_USDT:
            until = _now() + COOLDOWN_MIN * 60
            self.state.setdefault("cooldowns", {})[symbol] = until
        _save_state(self.state)

    def daily_kill_switch(self, equity_now_usdt: Optional[float]) -> (bool, str):
        self._ensure_day()
        if equity_now_usdt is None:
            return False, "n/a"
        if self.state.get("equity_start") is None:
            self.state["equity_start"] = equity_now_usdt
            self.state["equity_low"] = equity_now_usdt
            _save_state(self.state)
            return False, "init"
        eq0 = float(self.state["equity_start"])
        self.state["equity_low"] = min(float(self.state.get("equity_low", eq0)), equity_now_usdt)
        dd = (eq0 - float(self.state["equity_low"])) / max(1e-9, eq0) * 100.0
        _save_state(self.state)
        if dd >= DAILY_DD_LIMIT_PCT:
            return True, f"daily DD {dd:.2f}% ≥ {DAILY_DD_LIMIT_PCT}%"
        return False, "ok"
