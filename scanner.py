# -*- coding: utf-8 -*-
"""
scanner.py — scan H1/H4/D1/M15, logs détaillés par symbole, seuil insti adaptatif,
RR brut/net, sizing par risque, exécution SFI (SFIEngine), et anti-doublons.
"""

from __future__ import annotations
import os, json, time, math, logging
from typing import Dict, Any, List, Optional, Tuple
from datetime import datetime, timezone

import pandas as pd
import httpx

logging.basicConfig(level=os.environ.get("LOG_LEVEL", "INFO"),
                    format="%(asctime)s [%(levelname)s] %(message)s")
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("urllib3").setLevel(logging.WARNING)

LOG = logging.getLogger("runner")
LOG.info("runner: start")

# ---- Imports projet
from kucoin_utils import fetch_all_symbols, fetch_klines as _ku_get_klines  # type: ignore
from risk_sizing import valueqty_from_risk  # type: ignore
from rr_costs import rr_gross, rr_net       # type: ignore
import institutional_data as inst_data

# WebSocket Binance (liquidations temps réel)
try:
    import binance_ws
    binance_ws.start_ws_background()
    LOG.info("Binance WS démarré")
except Exception as e:
    LOG.warning("Binance WS KO: %s", e)

# Metrics CSV (optionnel)
try:
    from metrics import log_signal, log_order  # type: ignore
except Exception:
    def log_signal(*args, **kwargs): pass
    def log_order(*args, **kwargs): pass

# Bridge d'analyse
try:
    import analyze_bridge as analyze_mod  # type: ignore
except Exception:
    import analyze_signal as analyze_mod  # type: ignore

# SFI & perf
from execution_sfi import SFIEngine  # type: ignore
try:
    from perf_metrics import register_signal_perf, update_perf_for_symbol  # type: ignore
except Exception:
    def register_signal_perf(*args, **kwargs): pass
    def update_perf_for_symbol(*args, **kwargs): pass

# Log décision structuré (optionnel -> fallback no-op)
try:
    from decision_logger import log_institutional, log_tech, log_macro, log_decision  # type: ignore
except Exception:
    def log_institutional(*a, **k): pass
    def log_tech(*a, **k): pass
    def log_macro(*a, **k): pass
    def log_decision(*a, **k): pass

# Telegram
TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID")
def send_telegram(text: str, parse_mode: str = "Markdown"):
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        LOG.info("[TG OFF] %s", text); return
    try:
        url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
        httpx.post(url, json={"chat_id": TELEGRAM_CHAT_ID, "text": text, "parse_mode": parse_mode,
                              "disable_web_page_preview": True}, timeout=10)
    except Exception as e:
        LOG.error("Telegram KO: %s", e)

# ---- Helpers ENV robustes
def _env_float(name: str, default: float) -> float:
    v = os.environ.get(name)
    if v in (None, "", "null", "None"): return float(default)
    try: return float(v)
    except Exception: return float(default)

def _env_int(name: str, default: int) -> int:
    v = os.environ.get(name)
    if v in (None, "", "null", "None"): return int(default)
    try: return int(v)
    except Exception: return int(default)

# ---- ENV
SENT_SIGNALS_PATH = os.environ.get("SENT_SIGNALS_PATH", "sent_signals.json")
DUP_TTL_HOURS = _env_float("DUP_TTL_HOURS", 24.0)

VALUE_USDT = _env_float("ORDER_VALUE_USDT", 20.0)
RISK_PER_TRADE_USDT = _env_float("RISK_PER_TRADE_USDT", 0.0)
MIN_NOTIONAL_USDT = _env_float("MIN_NOTIONAL_USDT", 5.0)

MACRO_TTL_SECONDS = _env_int("MACRO_TTL_SECONDS", 120)
H1_LIMIT = _env_int("H1_LIMIT", 500)
H4_LIMIT = _env_int("H4_LIMIT", 400)
D1_LIMIT = _env_int("D1_LIMIT", 200)
M15_LIMIT = _env_int("M15_LIMIT", 200)

REQ_SCORE_FLOOR = _env_float("REQ_SCORE_FLOOR", 1.2)
INST_Q = _env_float("INST_Q", 0.70)
INST_WINDOW = _env_int("INST_WINDOW", 200)
INST_STATS_PATH = os.environ.get("INST_STATS_PATH", "inst_stats.json")

AUTO_SYMBOLS = os.environ.get("AUTO_SYMBOLS", "1") == "1"
SYMBOLS = [s.strip() for s in os.environ.get("SYMBOLS", "BTCUSDTM,ETHUSDTM,SOLUSDTM").split(",") if s.strip()]
SYMBOLS_MAX = _env_int("SYMBOLS_MAX", 450)

LOG_DETAIL = os.environ.get("LOG_DETAIL", "1") == "1"

# ---- Utils généraux
def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")

def fmt_price(x: Optional[float]) -> str:
    if x is None: return "—"
    if x == 0: return "0"
    d = 2 if x >= 1 else min(8, int(abs(math.log10(1.0/abs(x)))) + 2)
    return f"{x:.{d}f}"

def load_json(path: str) -> Dict[str, Any]:
    if not os.path.exists(path): return {}
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}

def save_json(path: str, data: Dict[str, Any]) -> None:
    try:
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
    except Exception as e:
        LOG.warning("save_json KO: %s", e)

def purge_old(store: Dict[str, Any], ttl_h: float):
    cutoff = time.time() - ttl_h * 3600.0
    for k in list(store.keys()):
        if store[k].get("ts", 0) < cutoff:
            store.pop(k, None)

def signal_key(symbol: str, side: str, entry: Optional[float], rr: Optional[float]) -> str:
    be = None if entry is None else round(float(entry), 4)
    br = None if rr is None else round(float(rr), 2)
    return f"{symbol}:{side}:{be}:{br}"

def _canon_symbol(sym: str) -> str:
    return sym.upper().replace("/", "").replace("-", "")

def _load_symbols() -> List[str]:
    if AUTO_SYMBOLS:
        return fetch_all_symbols(limit=SYMBOLS_MAX)
    return SYMBOLS

# ---- Caches/Classes
class MacroCache:
    def __init__(self, ttl: int = MACRO_TTL_SECONDS):
        self.ttl = ttl; self._snap=None; self._ts=0.0
    def snapshot(self) -> Dict[str, Any]:
        if self._snap and (time.time()-self._ts)<self.ttl:
            return self._snap
        self._snap = {
            "TOTAL": inst_data.get_macro_total_mcap(),
            "TOTAL2": inst_data.get_macro_total2(),
            "BTC_DOM": inst_data.get_macro_btc_dominance(),
        }
        self._ts = time.time()
        return self._snap

class InstThreshold:
    def __init__(self, path=INST_STATS_PATH, window=INST_WINDOW, q=INST_Q, floor=REQ_SCORE_FLOOR):
        self.path, self.window, self.q, self.floor = path, window, q, floor
        self.scores = self._load()
    def _load(self) -> List[float]:
        if not os.path.exists(self.path): return []
        try:
            data = json.load(open(self.path, "r", encoding="utf-8"))
            return [float(x) for x in data.get("scores", [])]
        except Exception:
            return []
    def _save(self):
        try:
            json.dump({"scores": self.scores}, open(self.path, "w", encoding="utf-8"),
                      ensure_ascii=False, indent=2)
        except Exception as e:
            LOG.error("InstThreshold save KO: %s", e)
    def add(self, score: Optional[float]):
        if score is None: return
        try: s = float(score)
        except Exception: return
        self.scores.append(s)
        if len(self.scores) > self.window:
            self.scores = self.scores[-self.window:]
        self._save()
    def threshold(self) -> float:
        if not self.scores: return self.floor
        arr = sorted(self.scores)
        k = max(0, min(len(arr)-1, int(math.ceil(self.q * len(arr)) - 1)))
        return max(arr[k], self.floor)

# ---- Analyse d'un symbole (MTF strict)
def analyze_one(symbol: str, macro: MacroCache, gate: InstThreshold) -> Tuple[Optional[Dict[str, Any]], Optional[str]]:
    df_h1 = _ku_get_klines(symbol, "1h", H1_LIMIT)
    df_h4 = _ku_get_klines(symbol, "4h", H4_LIMIT)
    df_d1 = _ku_get_klines(symbol, "1d", D1_LIMIT)
    df_m15 = _ku_get_klines(symbol, "15m", M15_LIMIT)
    if df_h1.empty or df_h4.empty or df_d1.empty or df_m15.empty:
        return None, "bars vides (fetch KO)"

    try:
        inst_snap = inst_data.build_institutional_snapshot(symbol)
        LOG.info("[%s] inst_snap: %s", symbol, inst_snap)
        res_raw = analyze_mod.analyze_signal(symbol=_canon_symbol(symbol),
                                             df_h1=df_h1, df_h4=df_h4,
                                             df_d1=df_d1, df_m15=df_m15,
                                             inst=inst_snap,
                                             macro=macro.snapshot())
    except Exception as e:
        return None, f"analyze_signal error: {e}"

    res = res_raw if isinstance(res_raw, dict) else {}
    res.setdefault("inst_score", inst_snap.get("score", 0.0))
    res.setdefault("inst_ok_count", 0)
    return res, None

# ---- Boucle principale
def scan_and_send_signals(symbols: Optional[List[str]] = None) -> Dict[str, Any]:
    macro = MacroCache()
    gate = InstThreshold()
    try:
        if symbols is None:
            symbols = _load_symbols()
    except Exception:
        symbols = SYMBOLS

    store = load_json(SENT_SIGNALS_PATH)
    purge_old(store, DUP_TTL_HOURS)

    scanned = 0; sent = 0; errors = 0
    for sym in symbols:
        scanned += 1
        res, err = analyze_one(sym, macro, gate)
        if err:
            LOG.info("[%s] %s", sym, err); errors += 1; continue
        if not res:
            continue
        LOG.info("[%s] decision: %s", sym, res)
    return {"scanned": scanned, "sent": sent, "errors": errors, "ts": now_iso()}

if __name__ == "__main__":
    out = scan_and_send_signals()
    print(out)
