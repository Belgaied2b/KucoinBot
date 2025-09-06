# -*- coding: utf-8 -*-
"""
scanner.py — scan H1/H4, logs détaillés par symbole, seuil insti adaptatif,
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
# Couper le bruit réseau verbeux
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("urllib3").setLevel(logging.WARNING)

LOG = logging.getLogger("runner")
LOG.info("runner: start")

# ---- Imports projet
from kucoin_utils import fetch_all_symbols  # type: ignore
from risk_sizing import valueqty_from_risk  # type: ignore
from rr_costs import rr_gross, rr_net       # type: ignore

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

# Seuil insti adaptatif (quantile)
REQ_SCORE_FLOOR = _env_float("REQ_SCORE_FLOOR", 1.2)
INST_Q = _env_float("INST_Q", 0.70)
INST_WINDOW = _env_int("INST_WINDOW", 200)
INST_STATS_PATH = os.environ.get("INST_STATS_PATH", "inst_stats.json")

AUTO_SYMBOLS = os.environ.get("AUTO_SYMBOLS", "1") == "1"
# ⚠️ KuCoin Futures accepte bien BTCUSDTM/ETHUSDTM/SOLUSDTM
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

# ---- KuCoin Futures Klines (from/to en millisecondes)
KU_FUT_BASE = "https://api-futures.kucoin.com"

_GRAN_MIN = {
    "1m": 1, "3m": 3, "5m": 5, "15m": 15, "30m": 30,
    "1h": 60, "2h": 120, "4h": 240, "8h": 480, "12h": 720,
    "1d": 1440, "1w": 10080, "1mo": 43200
}

def _canon_symbol(sym: str) -> str:
    s = sym.upper()
    # Ajouter M si ça finit en USDT sans M
    if s.endswith("USDT") and not s.endswith("USDTM"):
        s = s + "M"
    return s

def _ku_get_klines(symbol: str, interval: str, limit: int) -> pd.DataFrame:
    """KuCoin Futures /api/v1/kline/query (granularity en minutes, from/to en millisecondes)."""
    gran = _GRAN_MIN.get(interval)
    if not gran:
        raise ValueError(f"interval inconnu: {interval}")

    sym = _canon_symbol(symbol)
    now_ms = int(time.time() * 1000)

    # Certaines configs 400 si la fenêtre est trop large → on réessaie en réduisant la fenêtre.
    # On limite aussi le nb max de points à 1500 par sécurité.
    max_pts = 1500
    req_pts = min(int(limit), max_pts)

    for shrink in (1.0, 0.5, 0.25, 0.125):
        span_ms = int(gran * req_pts * 60_000 * shrink)  # minutes -> ms
        params = {
            "symbol": sym,
            "granularity": gran,      # minutes
            "from": now_ms - span_ms, # ms
            "to": now_ms              # ms
        }
        try:
            r = httpx.get(f"{KU_FUT_BASE}/api/v1/kline/query", params=params, timeout=15)
            r.raise_for_status()
            js = r.json()
            data = js.get("data") or []
        except httpx.HTTPStatusError as e:
            if e.response is not None and e.response.status_code == 400:
                LOG.warning("[%s] kline query 400 → shrink=%.3f params=%s", sym, shrink, params)
                time.sleep(0.3)
                continue
            LOG.warning("[%s] kline query KO: %s", sym, e)
            return pd.DataFrame()
        except Exception as e:
            LOG.warning("[%s] kline query KO: %s", sym, e)
            return pd.DataFrame()

        if not data:
            # Essaye rétrécir encore
            LOG.warning("[%s] kline vide (shrink=%.3f) → on réduit encore", sym, shrink)
            time.sleep(0.2)
            continue

        # data: [time, open, close, high, low, volume, turnover] ; time en ms normalement
        rows = []
        for row in data:
            t, o, c, h, l, v, _ = row
            t = int(float(t))
            # au cas où ce serait en secondes (rare), on convertit en ms pour DatetimeIndex, puis on redivise
            if t < 10**12:  # secondes -> ms
                t *= 1000
            rows.append((t, float(o), float(h), float(l), float(c), float(v)))

        df = pd.DataFrame(rows, columns=["time_ms", "open", "high", "low", "close", "volume"])
        df.sort_values("time_ms", inplace=True)
        df["time"] = (df["time_ms"] // 1000).astype(int)
        df = df[["time", "open", "high", "low", "close", "volume"]].reset_index(drop=True)

        # On coupe à 'limit' bougies les plus récentes si besoin
        if len(df) > limit:
            df = df.iloc[-limit:].reset_index(drop=True)
        return df

    # tous les essais vides/400
    LOG.warning("[%s] kline query épuisée (interval=%s, limit=%s)", sym, interval, limit)
    return pd.DataFrame()

# ---- Caches/Classes
class MacroCache:
    def __init__(self, ttl: int = MACRO_TTL_SECONDS):
        self.ttl = ttl; self._snap=None; self._ts=0.0
    def snapshot(self) -> Dict[str, Any]:
        if self._snap and (time.time()-self._ts)<self.ttl:
            return self._snap
        # TODO: brancher ta vraie macro (TOTAL/TOTAL2/DOM, etc.)
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
    if isinstance(obj, dict):
        return obj
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
        d["valid"] = (str(d["side"]).upper() != "NONE")
        diag = None
        if "diagnostics" in manage:
            diag = manage["diagnostics"]
        elif hasattr(obj, "diagnostics"):
            diag = getattr(obj, "diagnostics")
        if diag:
            d["diagnostics"] = diag
            inst_diag = (diag.get("inst") or {})
            d["inst_score"] = inst_diag.get("score", d.get("score"))
            d["inst_ok_count"] = inst_diag.get("components_ok_count")
        return d
    except Exception:
        return {"valid": False, "side": "NONE"}

def _value_usdt_for_order(entry: float, sl: float) -> float:
    if RISK_PER_TRADE_USDT > 0.0 and entry and sl and float(entry) != float(sl):
        try:
            v = valueqty_from_risk(entry, sl, RISK_PER_TRADE_USDT)
            return max(MIN_NOTIONAL_USDT, float(v))
        except Exception:
            return VALUE_USDT
    return VALUE_USDT

def _load_symbols() -> List[str]:
    if not AUTO_SYMBOLS and SYMBOLS:
        return [_canon_symbol(s) for s in SYMBOLS]
    try:
        syms = [s for s in fetch_all_symbols(limit=SYMBOLS_MAX) if s.endswith("USDTM")]
        if not syms:
            LOG.warning("fetch_all_symbols vide — fallback SYMBOLS")
            return [_canon_symbol(s) for s in SYMBOLS]
        return [_canon_symbol(s) for s in syms]
    except Exception as e:
        LOG.warning("fetch_all_symbols erreur: %s — fallback SYMBOLS", e)
        return [_canon_symbol(s) for s in SYMBOLS]

# ---- Analyse d'un symbole
def analyze_one(symbol: str, macro: MacroCache, gate: InstThreshold) -> Tuple[Optional[Dict[str, Any]], Optional[str]]:
    # Bougies KuCoin Futures (from/to en ms)
    df_h1 = _ku_get_klines(symbol, "1h", H1_LIMIT)
    df_h4 = _ku_get_klines(symbol, "4h", H4_LIMIT)
    if df_h1.empty or df_h4.empty:
        return None, "bars vides (fetch KO)"

    # Call analyzer (supporte bridge et direct)
    try:
        res_raw = analyze_mod.analyze_signal(symbol=_canon_symbol(symbol), df_h1=df_h1, df_h4=df_h4, macro=macro.snapshot())
    except TypeError:
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
        comps_req = (inst_diag.get("thresholds") or {}).get("components_min", 2)
        details = (inst_diag.get("components_ok") or {})
        extras = {}
        for k in ("atr_pct", "quantile", "cvd", "liq5m", "book_imbal", "risk_on", "risk_off"):
            if k in inst_diag: extras[k] = inst_diag[k]
        log_institutional(symbol, inst_score, req=(inst_diag.get("req_score_min") or REQ_SCORE_FLOOR),
                          comps_ok=inst_ok_count, comps_req=comps_req, details=details, extras=extras)
        log_macro(symbol, macro_diag if macro_diag else macro.snapshot())
        log_tech(symbol, tech_diag, tolerated=res.get("tolerated"))

    # Validation principale (si non fournie par l'analyse)
    valid = bool(res.get("valid", False))
    rr = res.get("rr")
    dyn_thr = gate.threshold()

    if not valid:
        # Règle secours: ≥2 composants insti OK, RR≥1.2, score≥seuil adaptatif
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
        symbols = [_canon_symbol(s) for s in SYMBOLS]

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
