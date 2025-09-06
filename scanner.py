# -*- coding: utf-8 -*-
"""
scanner.py — scan H1/H4, logs détaillés par symbole, seuil insti adaptatif,
RR brut/net, sizing par risque, exécution SFI (SFIEngine), et anti-doublons.

Dépendances internes attendues:
- kucoin_utils.fetch_all_symbols, kucoin_utils.fetch_klines
- analyze_bridge.analyze_signal (ou analyze_signal.analyze_signal)
- decision_logger.log_institutional/log_tech/log_macro/log_decision (optionnel: fallback no-op)
- rr_costs.rr_gross/rr_net
- risk_sizing.valueqty_from_risk
- execution_sfi.SFIEngine
- perf_metrics.register_signal_perf/update_perf_for_symbol (optionnel)
"""

from __future__ import annotations
import os, json, time, math, logging
from typing import Dict, Any, List, Optional, Tuple
from datetime import datetime

logging.basicConfig(level=os.environ.get("LOG_LEVEL", "INFO"),
                    format="%(asctime)s [%(levelname)s] %(message)s")
# Couper le bruit réseau verbeux
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("urllib3").setLevel(logging.WARNING)

LOG = logging.getLogger("runner")

# ---- Imports projet
from kucoin_utils import fetch_all_symbols, fetch_klines
from risk_sizing import valueqty_from_risk
from rr_costs import rr_gross, rr_net

# Metrics CSV (optionnel)
try:
    from metrics import log_signal, log_order  # type: ignore
except Exception:
    def log_signal(*args, **kwargs): pass
    def log_order(*args, **kwargs): pass

# Bridge d'analyse (si dispo)
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
    import requests
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    try:
        requests.post(url, json={"chat_id": TELEGRAM_CHAT_ID, "text": text, "parse_mode": parse_mode,
                                 "disable_web_page_preview": True}, timeout=10)
    except Exception as e:
        LOG.error("Telegram KO: %s", e)

# ---- ENV
SENT_SIGNALS_PATH = os.environ.get("SENT_SIGNALS_PATH", "sent_signals.json")
DUP_TTL_HOURS = float(os.environ.get("DUP_TTL_HOURS", "24"))

VALUE_USDT = float(os.environ.get("ORDER_VALUE_USDT", "20.0"))
RISK_PER_TRADE_USDT = float(os.environ.get("RISK_PER_TRADE_USDT", "0"))
MIN_NOTIONAL_USDT = float(os.environ.get("MIN_NOTIONAL_USDT", "5.0"))

MACRO_TTL_SECONDS = int(os.environ.get("MACRO_TTL_SECONDS", "120"))
H1_LIMIT = int(os.environ.get("H1_LIMIT", "500"))
H4_LIMIT = int(os.environ.get("H4_LIMIT", "400"))

# Seuil insti adaptatif (quantile)
REQ_SCORE_FLOOR = float(os.environ.get("REQ_SCORE_FLOOR", "1.2"))
INST_Q = float(os.environ.get("INST_Q", "0.70"))
INST_WINDOW = int(os.environ.get("INST_WINDOW", "200"))
INST_STATS_PATH = os.environ.get("INST_STATS_PATH", "inst_stats.json")

AUTO_SYMBOLS = os.environ.get("AUTO_SYMBOLS", "1") == "1"
SYMBOLS = [s.strip() for s in os.environ.get("SYMBOLS", "BTCUSDT,ETHUSDT,SOLUSDT").split(",") if s.strip()]
SYMBOLS_MAX = int(os.environ.get("SYMBOLS_MAX", "450"))

LOG_DETAIL = os.environ.get("LOG_DETAIL", "1") == "1"

# ---- Utils
def now_iso() -> str: return datetime.utcnow().isoformat(timespec="seconds") + "Z"

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

class MacroCache:
    def __init__(self, ttl: int = MACRO_TTL_SECONDS):
        self.ttl = ttl; self._snap=None; self._ts=0.0
    def snapshot(self) -> Dict[str, Any]:
        if self._snap and (time.time()-self._ts)<self.ttl:
            return self._snap
        # TODO: Brancher ici ta vraie macro (TOTAL/TOTAL2/DOM, etc.)
        self._snap = {}
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
        try:
            s = float(score)
        except Exception:
            return
        self.scores.append(s)
        if len(self.scores) > self.window:
            self.scores = self.scores[-self.window:]
        self._save()
    def threshold(self) -> float:
        if not self.scores: return self.floor
        arr = sorted(self.scores)
        k = max(0, min(len(arr)-1, int(math.ceil(self.q * len(arr)) - 1)))
        return max(arr[k], self.floor)

def build_msg(symbol: str, res: Dict[str, Any]) -> str:
    tol = ", ".join(res.get("tolerated", [])) if res.get("tolerated") else ""
    return (
        f"⚡ *{symbol}* — *{str(res.get('side','?')).upper()}*\n"
        f"RR: *{res.get('rr','—')}* • Entrée: *{fmt_price(res.get('entry'))}* • "
        f"SL: *{fmt_price(res.get('sl'))}* • TP1: *{fmt_price(res.get('tp1'))}* • TP2: *{fmt_price(res.get('tp2'))}*\n"
        f"Inst.Score: *{res.get('inst_score','—')}* (OK: *{res.get('inst_ok_count','—')}*)"
        + (f"\nTolérés: {tol}" if tol else "")
        + f"\n_UTC: {now_iso()}_"
    )

# ---- Adapters (compat dict / dataclass Decision)
def _decision_to_dict(obj: Any) -> Dict[str, Any]:
    """Accepte un dict ou un dataclass Decision -> dict unifié pour scanner."""
    if isinstance(obj, dict):
        return obj
    # dataclass Decision (side, name, reason, tolerated, rr, entry, sl, tp1, tp2, score, manage)
    try:
        d = {
            "side": getattr(obj, "side", "NONE"),
            "name": getattr(obj, "name", "setup"),
            "reason": getattr(obj, "reason", ""),
            "tolerated": list(getattr(obj, "tolerated", []) or []),
            "rr": float(getattr(obj, "rr", 0.0) or 0.0),
            "entry": float(getattr(obj, "entry", 0.0) or 0.0),
            "sl": float(getattr(obj, "sl", 0.0) or 0.0),
            "tp1": float(getattr(obj, "tp1", 0.0) or 0.0),
            "tp2": float(getattr(obj, "tp2", 0.0) or 0.0),
            "score": float(getattr(obj, "score", 0.0) or 0.0),
        }
        manage = getattr(obj, "manage", {}) or {}
        if isinstance(manage, dict):
            d["manage"] = manage
        # valid si side != NONE
        d["valid"] = (str(d["side"]).upper() != "NONE")
        # diagnostics
        diag = None
        if "diagnostics" in manage:
            diag = manage["diagnostics"]
        elif hasattr(obj, "diagnostics"):
            diag = getattr(obj, "diagnostics")
        if diag:
            d["diagnostics"] = diag
            # inst fields extraction
            inst_diag = (diag.get("inst") or {})
            d["inst_score"] = inst_diag.get("score", d.get("score"))
            d["inst_ok_count"] = inst_diag.get("components_ok_count")
        return d
    except Exception:
        return {"valid": False, "side": "NONE"}

def _value_usdt_for_order(entry: float, sl: float) -> float:
    """Sizing par risque si configuré, sinon valeur fixe."""
    if RISK_PER_TRADE_USDT > 0.0 and entry and sl and float(entry) != float(sl):
        try:
            v = valueqty_from_risk(entry, sl, RISK_PER_TRADE_USDT)
            return max(MIN_NOTIONAL_USDT, float(v))
        except Exception:
            return VALUE_USDT
    return VALUE_USDT

def _load_symbols() -> List[str]:
    if not AUTO_SYMBOLS and SYMBOLS:
        return SYMBOLS
    try:
        syms = [s for s in fetch_all_symbols(limit=SYMBOLS_MAX) if s.endswith("USDTM")]
        if not syms:
            LOG.warning("fetch_all_symbols vide — fallback SYMBOLS")
            return SYMBOLS
        return syms
    except Exception as e:
        LOG.warning("fetch_all_symbols erreur: %s — fallback SYMBOLS", e)
        return SYMBOLS

# ---- Analyse d'un symbole
def analyze_one(symbol: str, macro: MacroCache, gate: InstThreshold) -> Tuple[Optional[Dict[str, Any]], Optional[str]]:
    # Bars
    try:
        df_h1 = fetch_klines(symbol, "1h", H1_LIMIT)
        df_h4 = fetch_klines(symbol, "4h", H4_LIMIT)
    except Exception as e:
        return None, f"fetch_klines KO: {e}"

    # Call analyzer (supporte bridge et direct)
    try:
        res_raw = analyze_mod.analyze_signal(symbol=symbol, df_h1=df_h1, df_h4=df_h4, macro=macro.snapshot())
    except TypeError:
        # signature différente
        res_raw = analyze_mod.analyze_signal(df_h1, df_h4)

    res = _decision_to_dict(res_raw)
    if not isinstance(res, dict):
        return None, "analyze_signal renvoie non-dict"

    # diagnostics pour logs
    diag = res.get("diagnostics") or (res.get("manage", {}) or {}).get("diagnostics") or {}
    inst_diag = diag.get("inst") or {}
    tech_diag = diag.get("tech") or {}
    macro_diag = diag.get("macro") or {}

    # champs insti unifiés
    inst_score = float(res.get("inst_score", inst_diag.get("score", res.get("score", 0.0)) or 0.0))
    inst_ok_count = int(res.get("inst_ok_count", inst_diag.get("components_ok_count", 0)) or 0)

    # Logs détaillés
    if LOG_DETAIL:
        # institutionnel
        comps_req = (inst_diag.get("thresholds") or {}).get("components_min", 2)
        details = (inst_diag.get("components_ok") or {})
        extras = {}
        for k in ("atr_pct", "quantile", "cvd", "liq5m", "book_imbal", "risk_on", "risk_off"):
            if k in inst_diag:
                extras[k] = inst_diag[k]
        log_institutional(symbol, inst_score, req=(inst_diag.get("req_score_min") or REQ_SCORE_FLOOR),
                          comps_ok=inst_ok_count, comps_req=comps_req, details=details, extras=extras)
        # macro (si dispo)
        log_macro(symbol, macro_diag if macro_diag else macro.snapshot())
        # technique
        log_tech(symbol, tech_diag, tolerated=res.get("tolerated"))

    # Validation principale (si non fournie par l'analyse)
    valid = bool(res.get("valid", False))
    rr = res.get("rr")
    dyn_thr = gate.threshold()

    if not valid:
        # Règle “pro” de secours: ≥2 composants insti OK, RR≥1.2, score≥seuil adaptatif
        if (inst_ok_count >= 2) and (rr is not None and rr >= 1.2) and (inst_score >= dyn_thr):
            res["valid"] = True
            res.setdefault("tolerated", [])
            if rr is not None and rr < 1.5 and "RR" not in res["tolerated"]:
                res["tolerated"].append("RR")
            res.setdefault("comments", []).append(
                f"Validation institutionnelle (seuil adaptatif {dyn_thr:.2f}): ≥2 indicateurs OK et RR ≥ 1.2"
            )

    # Màj stats quantile
    gate.add(inst_score)
    # Ajout pour logs décision
    res.setdefault("inst_score", inst_score)
    res.setdefault("inst_ok_count", inst_ok_count)
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
            update_perf_for_symbol(sym); continue

        # Décision + logs finaux
        side = str(res.get("side", "none")).lower()
        entry = float(res.get("entry") or 0.0)
        sl = float(res.get("sl") or 0.0)
        tp1 = float(res.get("tp1") or 0.0)
        tp2 = float(res.get("tp2") or 0.0)
        rr = res.get("rr")
        score = float(res.get("inst_score", res.get("score", 0.0)) or 0.0)

        # RR brut/net (si possible)
        rr_g, rr_n = 0.0, 0.0
        try:
            if entry and sl and tp1 and float(entry) != float(sl):
                rr_g = rr_gross(entry, sl, tp1, side)
                rr_n = rr_net(entry, sl, tp1, side, fill_mode="maker")
        except Exception:
            pass

        accepted = bool(res.get("valid", False))
        diag = res.get("diagnostics") or (res.get("manage", {}) or {}).get("diagnostics") or {}
        reasons_blk = (diag.get("reasons_block") or [])

        log_decision(sym, accepted=accepted, reason_blocks=reasons_blk,
                     rr_gross=rr_g, rr_net=rr_n, side=side,
                     entry=entry, sl=sl, tp1=tp1, tp2=tp2, score=score)

        if not accepted:
            update_perf_for_symbol(sym)
            continue

        # Anti-doublon
        key = signal_key(sym, side, entry, rr)
        if key in store:
            LOG.info("[%s] doublon ignoré", sym)
            update_perf_for_symbol(sym)
            continue

        # Telegram
        try:
            send_telegram(build_msg(sym, res))
        except Exception as e:
            LOG.warning("[%s] Telegram msg KO: %s", sym, e)

        # Metrics “signal”
        try:
            log_signal(sym, side, float(score), float(rr_g), float(rr_n), "maker", note="accept")
        except Exception:
            pass

        # Exécution SFI
        try:
            value_usdt = _value_usdt_for_order(entry, sl)
            engine = SFIEngine(sym, side, float(value_usdt), sl, tp1, tp2)
            order_ids = engine.place_initial(entry_hint=entry)
            engine.maybe_requote()
            LOG.info("[%s] exec: placed=%s mode=post-only value=%.2f entry_hint=%s",
                     sym, order_ids, float(value_usdt), entry)
        except Exception as e:
            LOG.error("[%s] SFI KO: %s", sym, e)

        # Persistance doublon + perf
        store[key] = {"symbol": sym, "side": side, "rr": rr, "entry": entry, "ts": time.time()}
        save_json(SENT_SIGNALS_PATH, store)

        register_signal_perf(key, sym, side, entry)
        update_perf_for_symbol(sym)

        sent += 1

    summary = {"scanned": scanned, "sent": sent, "errors": errors, "ts": now_iso()}
    LOG.info("Scan: %s", summary)
    return summary

if __name__ == "__main__":
    out = scan_and_send_signals()
    print(out)
