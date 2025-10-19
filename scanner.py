# -*- coding: utf-8 -*-
"""
scanner.py — scan H1/H4/D1/M15, logs détaillés par symbole, seuil insti adaptatif,
RR brut/net, sizing par risque, exécution SFI puis fallback KuCoin, et anti-doublons.
"""

from __future__ import annotations
import os, json, time, math, logging
from typing import Dict, Any, List, Optional, Tuple
from datetime import datetime, timezone

import pandas as pd
import httpx

# =========================
# Logging (texte lisible)
# =========================
try:
    from logger_utils import get_logger
    LOG = get_logger("scanner")
except Exception:
    logging.basicConfig(level=os.environ.get("LOG_LEVEL", "INFO"),
                        format="%(asctime)s | %(levelname)-5s | %(name)s | %(message)s")
    LOG = logging.getLogger("scanner")

logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("urllib3").setLevel(logging.WARNING)
LOG.info("scanner: start")

# =========================
# Imports projet
# =========================
from kucoin_utils import fetch_all_symbols, fetch_klines as _ku_get_klines  # type: ignore
from rr_costs import rr_gross, rr_net       # type: ignore
from risk_sizing import valueqty_from_risk  # type: ignore
import institutional_data as inst_data
from institutional_data import get_required_score  # seuil mini BTC/ETH vs Alts

# Exécution avancée (Smart Fill) + Fallback KuCoin
from execution_sfi import SFIEngine  # type: ignore
try:
    from kucoin_adapter import (
        place_limit_order as kc_place_limit_order,
        get_symbol_meta as kc_get_symbol_meta,
        get_order_by_client_oid as kc_get_by_coid,  # optionnel
    )
except Exception:
    kc_place_limit_order = None  # type: ignore
    kc_get_symbol_meta = None    # type: ignore
    kc_get_by_coid = None        # type: ignore

# Bridge d’analyse
try:
    import analyze_bridge as analyze_mod  # type: ignore
except Exception:
    import analyze_signal as analyze_mod  # type: ignore

# Metrics CSV (optionnel) — patch: adaptateur de signature
try:
    from metrics import log_signal as _log_signal_metrics, log_order  # type: ignore
except Exception:
    def _log_signal_metrics(*a, **k): pass
    def log_order(*a, **k): pass

def _log_signal_safe(symbol: str, side: str, inst_score: float,
                     rr_g: float | None, rr_n: float | None, fill_mode: str = "maker"):
    """Adapte l'appel à metrics.log_signal(symbol, side, score, rr_gross, rr_net, fill_mode, note='')."""
    try:
        _log_signal_metrics(
            symbol=symbol,
            side=side,
            score=float(inst_score or 0.0),
            rr_gross=float(rr_g or 0.0),
            rr_net=float(rr_n or 0.0),
            fill_mode=fill_mode,
            note=""
        )
    except Exception:
        pass

# Perf (MFE/MAE) optionnel
try:
    from perf_metrics import register_signal_perf, update_perf_for_symbol  # type: ignore
except Exception:
    def register_signal_perf(*args, **kwargs): pass
    def update_perf_for_symbol(*args, **kwargs): pass

# WebSocket Binance (liquidations temps réel) — best-effort
try:
    import binance_ws
    binance_ws.start_ws_background()
    LOG.info("Binance WS démarré")
except Exception as e:
    LOG.warning("Binance WS KO: %s", e)

# =========================
# Telegram
# =========================
TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID   = os.environ.get("TELEGRAM_CHAT_ID")

def send_telegram(text: str, parse_mode: str = "Markdown"):
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        LOG.info("[TG OFF] %s", text)
        return
    try:
        url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
        httpx.post(url, json={
            "chat_id": TELEGRAM_CHAT_ID,
            "text": text,
            "parse_mode": parse_mode,
            "disable_web_page_preview": True
        }, timeout=10)
    except Exception as e:
        LOG.error("Telegram KO: %s", e)

# =========================
# Helpers ENV robustes
# =========================
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

# =========================
# ENV
# =========================
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
SYMBOLS = [s.strip().upper() for s in os.environ.get("SYMBOLS", "BTCUSDTM,ETHUSDTM,SOLUSDTM").split(",") if s.strip()]
SYMBOLS_MAX = _env_int("SYMBOLS_MAX", 450)

LOG_DETAIL = os.environ.get("LOG_DETAIL", "1") == "1"
KC_POST_ONLY_DEFAULT = os.environ.get("KC_POST_ONLY", "1") == "1"
KC_VERIFY_MAX_TRIES  = _env_int("KC_VERIFY_MAX_TRIES", 5)
KC_VERIFY_DELAY_SEC  = _env_float("KC_VERIFY_DELAY_SEC", 0.35)

# Seuil composantes insti (OI, Funding, Liq, CVD)
INST_OK_MIN = _env_float("INST_OK_MIN", 0.20)

# =========================
# Utils généraux
# =========================
def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")

def fmt_price(x: Optional[float]) -> str:
    if x is None: return "—"
    if x == 0: return "0"
    try:
        d = 2 if x >= 1 else min(8, int(abs(math.log10(1.0/abs(float(x))))) + 2)
        return f"{float(x):.{d}f}"
    except Exception:
        return str(x)

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
    return str(sym).upper().replace("/", "").replace("-", "")

# =========================
# Caches/Classes
# =========================
class MacroCache:
    def __init__(self, ttl: int = MACRO_TTL_SECONDS):
        self.ttl = ttl
        self._snap = None
        self._ts = 0.0
    def snapshot(self) -> Dict[str, Any]:
        if self._snap and (time.time() - self._ts) < self.ttl:
            return self._snap
        self._snap = {
            "TOTAL":   inst_data.get_macro_total_mcap(),
            "TOTAL2":  inst_data.get_macro_total2(),
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
    def threshold(self, symbol: str) -> float:
        base_req = get_required_score(symbol)
        if not self.scores:
            return max(self.floor, base_req)
        arr = sorted(self.scores)
        k = max(0, min(len(arr)-1, int(math.ceil(self.q * len(arr)) - 1)))
        return max(arr[k], self.floor, base_req)

# =========================
# Fetch util
# =========================
def _get_klines_all(symbol: str) -> Tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    df_h1  = _ku_get_klines(symbol, "1h",  H1_LIMIT)
    df_h4  = _ku_get_klines(symbol, "4h",  H4_LIMIT)
    df_d1  = _ku_get_klines(symbol, "1d",  D1_LIMIT)
    df_m15 = _ku_get_klines(symbol, "15m", M15_LIMIT)
    return df_h1, df_h4, df_d1, df_m15

# =========================
# Analyse d’un symbole
# =========================
def analyze_one(symbol: str, macro: MacroCache, gate: InstThreshold) -> Tuple[Optional[Dict[str, Any]], Optional[str]]:
    try:
        df_h1, df_h4, df_d1, df_m15 = _get_klines_all(symbol)
    except Exception as e:
        return None, f"fetch_klines error: {e}"

    for tf, df in (("H1", df_h1), ("H4", df_h4), ("D1", df_d1), ("M15", df_m15)):
        if getattr(df, "empty", False):
            return None, f"bars vides ({tf})"

    # ----- Snapshot institutionnel + Gate -----
    try:
        inst_snap = inst_data.build_institutional_snapshot(symbol)
    except Exception as e:
        LOG.warning("[%s] inst snapshot KO: %s", symbol, e)
        inst_snap = {}

    try:
        req   = gate.threshold(symbol)
        score = float(inst_snap.get("score", 0.0) or 0.0)

        oi_s   = float(inst_snap.get("oi_score", 0.0) or 0.0)
        fund_s = float(inst_snap.get("funding_score", 0.0) or 0.0)
        liq_s  = float(inst_snap.get("liq_new_score", 0.0) or 0.0)
        cvd_s  = float(inst_snap.get("cvd_score", 0.0) or 0.0)

        oi_ok   = oi_s   >= INST_OK_MIN
        fund_ok = fund_s >= INST_OK_MIN
        liq_ok  = liq_s  >= INST_OK_MIN
        cvd_ok  = cvd_s  >= INST_OK_MIN

        ok_count = int(oi_ok) + int(fund_ok) + int(liq_ok) + int(cvd_ok)

        tol_pass = False
        reason = "reject"
        passed = False
        if ok_count == 4:
            passed = True; tol_pass = True; reason = "force_pass_4of4"
        elif ok_count >= 3:
            passed = True; tol_pass = True; reason = "tolerance_pass_3of4"
        elif score >= req:
            passed = True; reason = "score_gate"

        LOG.info("[%s] INST-GATE pass=%s reason=%s score=%.2f req=%.2f comps=%d/4 "
                 "vals(oi=%.2f,cvd=%.2f,fund=%.2f,liq=%.2f) ok(oi=%s,cvd=%s,fund=%s,liq=%s)",
                 symbol, passed, reason, score, req, ok_count,
                 oi_s, cvd_s, fund_s, liq_s, oi_ok, cvd_ok, fund_ok, liq_ok)

    except Exception as e:
        # Si le gate explose, on laisse passer mais on log
        LOG.warning("[%s] inst gate KO: %s (bypass)", symbol, e)
        passed = True; reason = "gate_bypass"; tol_pass = False; score = 0.0; ok_count = 0

    # ----- Analyse principale -----
    try:
        res_raw = analyze_mod.analyze_signal(
            symbol=_canon_symbol(symbol),
            df_h1=df_h1, df_h4=df_h4, df_d1=df_d1, df_m15=df_m15,
            inst=inst_snap, macro=macro.snapshot()
        )
    except TypeError:
        # ancienne signature
        res_raw = analyze_mod.analyze_signal(symbol=_canon_symbol(symbol), df_h1=df_h1, df_h4=df_h4)
    except Exception as e:
        return None, f"analyze_signal error: {e}"

    res = res_raw if isinstance(res_raw, dict) else {}
    res["inst_score"] = score
    res["inst_ok_count"] = ok_count
    res["inst_tol_pass"] = tol_pass
    res["inst_pass"] = passed
    res["inst_pass_reason"] = reason
    return res, None

# =========================
# Normalisation d’ordres (pour logs/telegram)
# =========================
def _normalize_orders(orders: Optional[object]) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    if not orders: return out

    def _extract(d: Dict[str, Any]) -> Dict[str, Any]:
        x = dict(d)
        data = x.get("data")
        if isinstance(data, dict):
            x.setdefault("orderId",  data.get("orderId"))
            x.setdefault("clientOid", data.get("clientOid"))
        return x

    if isinstance(orders, dict):
        out.append(_extract(orders)); return out
    if isinstance(orders, list):
        for it in orders:
            if isinstance(it, dict): out.append(_extract(it))
            else: out.append({"raw": it})
        return out
    if isinstance(orders, str):
        out.append({"raw": orders}); return out
    return [{"raw": orders}]

def _has_real_order_id(orders: List[Dict[str, Any]]) -> bool:
    for o in orders or []:
        if not isinstance(o, dict): continue
        if o.get("orderId"): return True
        code = str(o.get("code", "")).strip()
        if o.get("ok") is True and code == "200000":
            if o.get("orderId") or o.get("clientOid"): return True
    return False

# =========================
# Exécution — SFI puis fallback KuCoin
# =========================
def _exec_via_sfi(symbol: str, side: str, entry: float, sl: float, tp1: float, tp2: float, notional: float) -> List[Dict[str, Any]]:
    # Compat différentes signatures de SFIEngine
    try:
        eng = SFIEngine(symbol, side, notional, sl, tp1, tp2)
    except TypeError:
        try:
            eng = SFIEngine(symbol, side, {"notional": notional, "sl": sl, "tp1": tp1, "tp2": tp2})
        except TypeError:
            eng = SFIEngine(symbol, side, notional, sl, tp1, tp2)

    # try open_limit / place_initial / place_from_decision / place_market
    for attempt in (
        ("open_limit", dict(entry=float(entry), sl=float(sl), tp1=float(tp1), tp2=float(tp2))),
        ("place_initial", dict(entry=float(entry), sl=float(sl), tp1=float(tp1), tp2=float(tp2))),
        ("place_initial", dict(entry_hint=float(entry))),
        ("place_from_decision", dict(decision={"entry": float(entry), "sl": float(sl), "tp1": float(tp1), "tp2": float(tp2)})),
        ("place_market", dict()),
    ):
        name, kwargs = attempt
        try:
            if hasattr(eng, name) and callable(getattr(eng, name)):
                LOG.info("[%s] SFI: try %s", symbol, name)
                res = getattr(eng, name)(**kwargs)  # type: ignore
                return _normalize_orders(res)
        except TypeError:
            # tente sans kwargs
            try:
                res = getattr(eng, name)()  # type: ignore
                return _normalize_orders(res)
            except Exception as e:
                LOG.info("[%s] SFI %s KO: %s", symbol, name, e)
        except Exception as e:
            LOG.info("[%s] SFI %s KO: %s", symbol, name, e)

    LOG.info("[%s] SFI: aucune méthode n'a abouti", symbol)
    return []

def _round_to_tick(px: float, tick: float) -> float:
    if not tick or tick <= 0: return float(px)
    return math.floor(float(px)/float(tick)) * float(tick)

def _exec_fallback_kucoin(symbol: str, side: str, entry: float, sl: float, tp1: float, tp2: float, notional: float) -> List[Dict[str, Any]]:
    if kc_place_limit_order is None:
        LOG.error("[%s] Fallback KuCoin indisponible (kucoin_adapter manquant).", symbol)
        return []

    try:
        meta = kc_get_symbol_meta(symbol) if kc_get_symbol_meta else {}
        tick = float((meta or {}).get("priceIncrement", 0.0) or 0.0)
    except Exception:
        tick = 0.0

    entry_px = _round_to_tick(entry, tick)
    side_api = "buy" if side.lower() in ("long","buy") else "sell"

    LOG.info("[%s] EXEC-KC fallback LIMIT px=%s tick=%s postOnly=%s", symbol, fmt_price(entry_px), tick, KC_POST_ONLY_DEFAULT)
    kc = kc_place_limit_order(  # type: ignore
        symbol=symbol, side=side_api, price=float(entry_px),
        value_usdt=float(notional), sl=float(sl), tp1=float(tp1), tp2=float(tp2),
        post_only=KC_POST_ONLY_DEFAULT
    )
    orderId   = None
    clientOid = None
    ok_flag   = False
    kc_code   = None
    msg       = None

    if isinstance(kc, dict):
        orderId   = kc.get("orderId") or (kc.get("data") or {}).get("orderId")
        clientOid = kc.get("clientOid") or (kc.get("data") or {}).get("clientOid")
        ok_flag   = bool(kc.get("ok", False))
        kc_code   = kc.get("code")
        msg       = kc.get("msg")
        LOG.info("[%s] EXEC-KC retour ok=%s code=%s msg=%s clientOid=%s orderId=%s",
                 symbol, ok_flag, kc_code, str(msg)[:120] if msg else None, clientOid, orderId)

    # Vérif clientOid si 200000 sans orderId (optionnel)
    if (not orderId) and clientOid and ok_flag and kc_code == "200000" and kc_get_by_coid:
        for _ in range(KC_VERIFY_MAX_TRIES):
            time.sleep(KC_VERIFY_DELAY_SEC)
            try:
                od = kc_get_by_coid(clientOid)  # type: ignore
            except Exception as e:
                LOG.info("[%s] verify clientOid error: %s", symbol, e)
                od = None
            if od and isinstance(od, dict):
                orderId = od.get("orderId") or od.get("id")
                status  = od.get("status") or od.get("state")
                LOG.info("[%s] EXEC-KC verify clientOid=%s status=%s orderId=%s", symbol, clientOid, status, orderId)
                if orderId:
                    break

    if orderId:
        return [{"ok": True, "orderId": orderId, "clientOid": clientOid, "code": kc_code or "200000"}]
    if ok_flag and kc_code == "200000" and clientOid:
        return [{"ok": True, "orderId": None, "clientOid": clientOid, "code": "200000"}]

    LOG.error("[%s] EXEC-KC échec — voir raw", symbol)
    return [{"ok": False, "raw": kc}]

# =========================
# Boucle principale (scan)
# =========================
def _load_symbols() -> List[str]:
    if AUTO_SYMBOLS:
        try:
            return fetch_all_symbols(limit=SYMBOLS_MAX)
        except Exception as e:
            LOG.warning("AUTO_SYMBOLS KO: %s — fallback SYM env", e)
            return SYMBOLS
    return SYMBOLS

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
        try:
            res, err = analyze_one(sym, macro, gate)
        except Exception as e:
            err = f"analyze_one crash: {e}"
        if err:
            LOG.info("[%s] %s", sym, err); errors += 1; continue
        if not res:
            continue

        side = str(res.get("side", "none")).lower()
        rr   = float(res.get("rr", 0) or 0.0)
        entry= res.get("entry", None)
        sl   = res.get("sl", None)
        tp1  = res.get("tp1", None)
        tp2  = res.get("tp2", None)

        comments = res.get("comments", []) or []
        diag = (res.get("manage", {}) or {}).get("diagnostics", {})
        tolerated = diag.get("tolerated", res.get("tolerated", []))

        LOG.info("[%s] ANALYZE: side=%s rr=%.2f inst_score=%.2f reason=%s comments=%s",
                 sym, side, rr, float(res.get("inst_score", 0) or 0.0),
                 res.get("reason") or "—", (", ".join(map(str, comments)) or "—"))

        # ----- RR brut / net correctement calculés -----
        try:
            if entry and sl and tp2:
                rr_b = rr_gross(float(entry), float(sl), float(tp2), side)
            else:
                rr_b = None
        except Exception:
            rr_b = None
        try:
            if entry and sl and tp2 and side in ("long","short"):
                rr_n = rr_net(float(entry), float(sl), float(tp2), side)
            else:
                rr_n = None
        except Exception:
            rr_n = None

        if rr_b is not None or rr_n is not None:
            LOG.info("[%s] RR: rr_gross=%s rr_net=%s",
                     sym, f"{rr_b:.2f}" if rr_b is not None else "—",
                     f"{rr_n:.2f}" if rr_n is not None else "—")

        # ----- Signal invalide → pré-signal si insti OK
        if (not res.get("valid", False)) or (side not in ("long", "short")):
            LOG.info("[%s] NO-TRADE: rr=%.2f reason=%s tolerated=%s diag=%s",
                     sym, rr, res.get("reason", "—"), tolerated, diag)

            if res.get("inst_pass", False):
                pre_msg = (
                    f"🟡 *{sym}* — Pré-signal (insti OK: {res.get('inst_pass_reason')})\n"
                    f"*Inst score*: {float(res.get('inst_score',0) or 0):.2f} | *OK*: {res.get('inst_ok_count')}/4\n"
                    f"*RR*: {rr:.2f} | *Side*: {(side.upper() if side!='none' else 'NONE')}\n"
                    f"*Raison rejet*: {res.get('reason','—')}\n"
                    f"*Tolérances*: {tolerated if tolerated else '—'}"
                )
                send_telegram(pre_msg)

            try: update_perf_for_symbol(sym, df_h1=None)
            except Exception: pass
            continue

        # ----- Anti-doublons
        key = signal_key(sym, side, entry, rr or rr_b or rr_n)
        if key in store:
            LOG.info("[%s] DUP-SKIP key=%s", sym, key)
            continue

        # ----- Sizing par risque (fallback valeur fixe)
        notional = VALUE_USDT
        if RISK_PER_TRADE_USDT > 0 and entry and sl and float(entry) != float(sl):
            try:
                notional = max(MIN_NOTIONAL_USDT, valueqty_from_risk(float(entry), float(sl), float(RISK_PER_TRADE_USDT)))
            except Exception as e:
                LOG.warning("[%s] sizing par risque KO: %s -> fallback=%s", sym, e, VALUE_USDT)
                notional = VALUE_USDT

        # ----- Exécution: SFI puis fallback KuCoin si nécessaire
        if not (entry and sl and tp1 and tp2):
            LOG.info("[%s] NO-TRADE: targets incomplets (entry/sl/tp1/tp2 manquants)", sym)
            continue

        LOG.info("[%s] EXEC PREP: %s entry=%s sl=%s tp1=%s tp2=%s notional=%s",
                 sym, side.upper(), fmt_price(entry), fmt_price(sl), fmt_price(tp1), fmt_price(tp2), fmt_price(notional))

        orders = _exec_via_sfi(sym, side, float(entry), float(sl), float(tp1), float(tp2), float(notional))
        orders = _normalize_orders(orders)

        if not _has_real_order_id(orders):
            LOG.info("[%s] EXEC: fallback KuCoin (aucun orderId de SFI)", sym)
            orders = _exec_fallback_kucoin(sym, side, float(entry), float(sl), float(tp1), float(tp2), float(notional))
            orders = _normalize_orders(orders)

        LOG.info("[%s] EXEC RESULT: %s", sym, orders)

        # ----- Telegram (signal exécuté)
        ids = []
        for o in orders or []:
            if isinstance(o, dict):
                oid = o.get("orderId") or o.get("clientOid") or o.get("raw")
                if oid: ids.append(str(oid))
        ids_str = ", ".join(ids) if ids else "—"

        msg = (f"🧠 *{sym}* — *{side.upper()}*\n"
               f"RR: *{res.get('rr','—')}* | Entrée: *{fmt_price(entry)}* | SL: *{fmt_price(sl)}* | "
               f"TP1: *{fmt_price(tp1)}* | TP2: *{fmt_price(tp2)}*\n"
               f"Notional: *{fmt_price(notional)}* USDT | Orders: {ids_str}")
        send_telegram(msg)

        # ----- Metrics / persistance anti-doublons / perf
        try:
            _log_signal_safe(
                sym,
                side,
                float(res.get("inst_score", 0) or 0.0),
                rr_b,  # rr_gross si calculé
                rr_n,  # rr_net si calculé
                "maker"
            )
        except Exception:
            pass
        try:
            log_order(sym, side, float(entry), float(sl), float(tp1), float(tp2), float(notional), "SFI/KC", "sent")
        except Exception:
            pass

        store[key] = {"ts": time.time(), "side": side, "entry": entry, "rr": rr}
        save_json(SENT_SIGNALS_PATH, store)

        try:
            register_signal_perf(key, sym, side, float(entry or 0))
            update_perf_for_symbol(sym, df_h1=None)
        except Exception:
            pass

        sent += 1

    LOG.info("SCAN END: scanned=%s sent=%s errors=%s ts=%s", scanned, sent, errors, now_iso())
    return {"scanned": scanned, "sent": sent, "errors": errors, "ts": now_iso()}

# =========================
# Script
# =========================
if __name__ == "__main__":
    out = scan_and_send_signals()
    print(out)
