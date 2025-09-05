# -*- coding: utf-8 -*-
"""
scanner.py — mode scan + SFI + seuil institutionnel adaptatif + perf MFE/MAE
"""

from __future__ import annotations
import os, json, time, math, logging
from typing import Dict, Any, List, Optional, Tuple
from datetime import datetime

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")

from kucoin_utils import fetch_all_symbols, fetch_klines

# Utilise le bridge si dispo
try:
    import analyze_bridge as analyze_signal
except Exception:
    import analyze_signal  # fallback

# SFI & perf
from execution_sfi import SFIEngine
from perf_metrics import register_signal_perf, update_perf_for_symbol

# Telegram
TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID")
def send_telegram(text: str, parse_mode: str = "Markdown"):
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        logging.info("[TG OFF] %s", text); return
    import requests
    url=f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    try:
        requests.post(url, json={"chat_id": TELEGRAM_CHAT_ID, "text": text, "parse_mode":parse_mode,
                                 "disable_web_page_preview": True}, timeout=10)
    except Exception as e:
        logging.error("Telegram KO: %s", e)

SENT_SIGNALS_PATH = os.environ.get("SENT_SIGNALS_PATH", "sent_signals.json")
DUP_TTL_HOURS = float(os.environ.get("DUP_TTL_HOURS", 24))
VALUE_USDT = float(os.environ.get("ORDER_VALUE_USDT", 20.0))
MACRO_TTL_SECONDS = int(os.environ.get("MACRO_TTL_SECONDS", 120))
H1_LIMIT = int(os.environ.get("H1_LIMIT", 500))
H4_LIMIT = int(os.environ.get("H4_LIMIT", 400))

# Seuil institutionnel adaptatif
REQ_SCORE_FLOOR = float(os.environ.get("REQ_SCORE_FLOOR", "2.0"))
INST_Q = float(os.environ.get("INST_Q", "0.70"))
INST_WINDOW = int(os.environ.get("INST_WINDOW", "200"))
INST_STATS_PATH = os.environ.get("INST_STATS_PATH", "inst_stats.json")

def now_iso() -> str: return datetime.utcnow().isoformat(timespec="seconds") + "Z"
def fmt_price(x: Optional[float]) -> str:
    if x is None: return "—"
    if x == 0: return "0"
    d = 2 if x >= 1 else min(8, int(abs(math.log10(1.0/x))) + 2)
    return f"{x:.{d}f}"

def load_json(path: str) -> Dict[str, Any]:
    if not os.path.exists(path): return {}
    try:
        with open(path, "r", encoding="utf-8") as f: return json.load(f)
    except Exception: return {}

def save_json(path: str, data: Dict[str, Any]) -> None:
    with open(path, "w", encoding="utf-8") as f: json.dump(data, f, ensure_ascii=False, indent=2)

def purge_old(store: Dict[str, Any], ttl_h: float):
    cutoff = time.time() - ttl_h * 3600.0
    for k in list(store.keys()):
        if store[k].get("ts", 0) < cutoff:
            store.pop(k, None)

def signal_key(symbol: str, side: str, entry: Optional[float], rr: Optional[float]) -> str:
    be = None if entry is None else round(entry, 4)
    br = None if rr is None else round(rr, 2)
    return f"{symbol}:{side}:{be}:{br}"

class MacroCache:
    def __init__(self, ttl: int = MACRO_TTL_SECONDS):
        self.ttl = ttl; self._snap=None; self._ts=0.0
    def snapshot(self) -> Dict[str, Any]:
        if self._snap and (time.time()-self._ts)<self.ttl:
            return self._snap
        self._snap = {}; self._ts = time.time(); return self._snap

class InstThreshold:
    def __init__(self, path=INST_STATS_PATH, window=INST_WINDOW, q=INST_Q, floor=REQ_SCORE_FLOOR):
        self.path, self.window, self.q, self.floor = path, window, q, floor
        self.scores = self._load()
    def _load(self) -> List[float]:
        if not os.path.exists(self.path): return []
        try:
            data = json.load(open(self.path, "r", encoding="utf-8"))
            return [float(x) for x in data.get("scores", [])]
        except Exception: return []
    def _save(self):
        try:
            json.dump({"scores": self.scores}, open(self.path, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
        except Exception as e:
            logging.error("InstThreshold save KO: %s", e)
    def add(self, score: Optional[float]):
        if score is None: return
        try: s=float(score)
        except Exception: return
        self.scores.append(s)
        if len(self.scores)>self.window:
            self.scores=self.scores[-self.window:]
        self._save()
    def threshold(self) -> float:
        if not self.scores: return self.floor
        arr=sorted(self.scores)
        k = max(0, min(len(arr)-1, int(math.ceil(self.q * len(arr)) - 1)))
        return max(arr[k], self.floor)

def build_msg(symbol: str, res: Dict[str, Any]) -> str:
    tol = ", ".join(res.get("tolerated", [])) if res.get("tolerated") else ""
    return (
        f"⚡ *{symbol}* — *{res.get('side','?').upper()}*\n"
        f"RR: *{res.get('rr','—')}* • Entrée: *{fmt_price(res.get('entry'))}* • "
        f"SL: *{fmt_price(res.get('sl'))}* • TP1: *{fmt_price(res.get('tp1'))}* • TP2: *{fmt_price(res.get('tp2'))}*\n"
        f"Inst.Score: *{res.get('inst_score','—')}* (OK: *{res.get('inst_ok_count','—')}*)"
        + (f"\nTolérés: {tol}" if tol else "")
        + f"\n_UTC: {now_iso()}_"
    )

def analyze_one(symbol: str, macro: MacroCache, gate: InstThreshold) -> Tuple[Optional[Dict[str, Any]], Optional[str]]:
    try:
        df_h1 = fetch_klines(symbol, "1h", H1_LIMIT)
        df_h4 = fetch_klines(symbol, "4h", H4_LIMIT)
    except Exception as e:
        return None, f"fetch_klines KO: {e}"

    try:
        res = analyze_signal.analyze_signal(symbol=symbol, df_h1=df_h1, df_h4=df_h4, macro=macro.snapshot())
    except TypeError:
        res = analyze_signal.analyze_signal(df_h1, df_h4)
    if not isinstance(res, dict):
        return None, "analyze_signal renvoie non-dict"

    valid = bool(res.get("valid", False))
    rr = res.get("rr")
    inst_ok = int(res.get("inst_ok_count") or 0)
    inst_score = float(res.get("inst_score") or 0.0)
    dyn_thr = gate.threshold()

    if not valid:
        if inst_ok >= 2 and (rr is not None and rr >= 1.2) and inst_score >= dyn_thr:
            res["valid"] = True
            res.setdefault("tolerated", [])
            if rr is not None and rr < 1.5 and "RR" not in res["tolerated"]:
                res["tolerated"].append("RR")
            res.setdefault("comments", []).append(
                f"Validation institutionnelle (seuil adaptatif {dyn_thr:.2f}): ≥2 indicateurs OK et RR ≥ 1.2"
            )

    gate.add(inst_score)
    return res, None

def scan_and_send_signals(symbols: Optional[List[str]] = None) -> Dict[str, Any]:
    macro = MacroCache()
    gate = InstThreshold()
    try:
        if symbols is None:
            symbols = [s for s in fetch_all_symbols() if s.endswith("USDTM")]
    except Exception:
        symbols = []

    store = load_json(SENT_SIGNALS_PATH)
    purge_old(store, DUP_TTL_HOURS)

    scanned=0; sent=0; errors=0

    for sym in symbols:
        scanned += 1
        res, err = analyze_one(sym, macro, gate)
        if err:
            logging.info("[%s] %s", sym, err); errors += 1; continue
        if not res or not res.get("valid", False):
            update_perf_for_symbol(sym)
            continue

        side = str(res.get("side","long")).lower()
        rr = res.get("rr"); entry = res.get("entry")
        sl, tp1, tp2 = res.get("sl"), res.get("tp1"), res.get("tp2")

        key = signal_key(sym, side, entry, rr)
        if key in store:
            logging.info("[%s] doublon ignoré", sym)
            update_perf_for_symbol(sym)
            continue

        send_telegram(build_msg(sym, res))

        try:
            engine = SFIEngine(sym, side, VALUE_USDT, sl, tp1, tp2)
            order_ids = engine.place_initial(entry_hint=entry)
            engine.maybe_requote()
            logging.info("[%s] ordres SFI: %s", sym, order_ids)
        except Exception as e:
            logging.error("[%s] SFI KO: %s", sym, e)

        store[key] = {"symbol": sym, "side": side, "rr": rr, "entry": entry, "ts": time.time()}
        save_json(SENT_SIGNALS_PATH, store)

        register_signal_perf(key, sym, side, entry)
        update_perf_for_symbol(sym)

        sent += 1

    summary = {"scanned": scanned, "sent": sent, "errors": errors, "ts": now_iso()}
    logging.info("Scan: %s", summary)
    return summary

if __name__ == "__main__":
    out = scan_and_send_signals()
    print(out)
