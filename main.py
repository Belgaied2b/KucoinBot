# -*- coding: utf-8 -*-
"""
main.py — Boucle event-driven optimisée + logs détaillés
- Build des symboles via fetch_symbol_meta() (contrats USDTM)
- Institutionnel + autotune si présents (soft import)
- Logs clairs à chaque étape
"""

import os, asyncio, logging, math, time, inspect
from typing import Dict, Any, Tuple, List, Union

from ws_router import EventBus, PollingSource
from execution_sfi import SFIEngine
from risk_guard import RiskGuard
from meta_policy import MetaPolicy
from perf_metrics import register_signal_perf, update_perf_for_symbol
from kucoin_utils import fetch_klines, fetch_symbol_meta
from log_setup import init_logging, enable_httpx

# ---- Soft imports pour l'institutionnel / autotune (optionnels)
HAS_INST = True
try:
    from inst_enrich import get_institutional_snapshot  # type: ignore
except Exception:
    HAS_INST = False

HAS_TUNER = True
try:
    from inst_autotune import InstAutoTune, components_ok  # type: ignore
except Exception:
    HAS_TUNER = False

# ---- Analyse: bridge prioritaire, sinon fallback
try:
    import analyze_bridge as analyze_signal  # type: ignore
except Exception:
    import analyze_signal  # type: ignore

TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID   = os.environ.get("TELEGRAM_CHAT_ID")

H1_LIMIT                 = int(os.getenv("H1_LIMIT", "500"))
H4_LIMIT                 = int(os.getenv("H4_LIMIT", "400"))
H1_REFRESH_SEC           = int(os.getenv("H1_REFRESH_SEC", "60"))
H4_REFRESH_SEC           = int(os.getenv("H4_REFRESH_SEC", "300"))
ANALYSIS_MIN_INTERVAL_SEC= int(os.getenv("ANALYSIS_MIN_INTERVAL_SEC", "15"))
WS_POLL_SEC              = int(os.getenv("WS_POLL_SEC", "5"))

_KLINE_CACHE: Dict[str, Dict[str, Any]] = {}
_LAST_ANALYSIS_TS: Dict[str, float] = {}

log = logging.getLogger("runner")
TUNER = InstAutoTune() if HAS_TUNER else None  # type: ignore


# ------------------------
# Utils
# ------------------------
def fmt_price(x):
    if x is None: return "—"
    if x == 0: return "0"
    try:
        d = 2 if x >= 1 else min(8, int(abs(math.log10(1.0/float(x)))) + 2)
        return f"{float(x):.{d}f}"
    except Exception:
        return str(x)

def send_telegram(text: str):
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        log.info("[TG OFF] %s", text); return
    import requests
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    try:
        requests.post(url, json={"chat_id": TELEGRAM_CHAT_ID, "text": text,
                                 "parse_mode":"Markdown", "disable_web_page_preview": True}, timeout=10)
    except Exception as e:
        log.error("Telegram KO: %s", e)

def _get_klines_cached(symbol: str) -> Tuple[Any, Any]:
    now = time.time()
    ent = _KLINE_CACHE.get(symbol, {})
    need_h1 = ("h1" not in ent) or (now - ent.get("ts_h1", 0) > H1_REFRESH_SEC)
    need_h4 = ("h4" not in ent) or (now - ent.get("ts_h4", 0) > H4_REFRESH_SEC)

    if need_h1:
        ent["h1"] = fetch_klines(symbol, interval="1h", limit=H1_LIMIT)
        ent["ts_h1"] = now
        log.debug("H1 fetch", extra={"symbol": symbol})
    else:
        log.debug("H1 cache hit", extra={"symbol": symbol})

    if need_h4:
        ent["h4"] = fetch_klines(symbol, interval="4h", limit=H4_LIMIT)
        ent["ts_h4"] = now
        log.debug("H4 fetch", extra={"symbol": symbol})
    else:
        log.debug("H4 cache hit", extra={"symbol": symbol})

    _KLINE_CACHE[symbol] = ent
    return ent.get("h1"), ent.get("h4")

def _build_symbols() -> List[str]:
    """
    Construit la liste des contrats USDTM (ex: BTCUSDTM) à partir de fetch_symbol_meta().
    - Respecte l'env SYMBOLS="BTCUSDTM,ETHUSDTM" si fourni.
    """
    env_syms = os.getenv("SYMBOLS", "").strip()
    if env_syms:
        lst = [s.strip().upper() for s in env_syms.split(",") if s.strip()]
        return sorted(set(lst))
    meta = fetch_symbol_meta()  # clés = "BTCUSDT", valeurs -> {"symbol_api":"BTCUSDTM", ...}
    syms = []
    for v in meta.values():
        sym_api = str(v.get("symbol_api", "")).strip().upper()
        if sym_api.endswith("USDTM"):
            syms.append(sym_api)
    return sorted(set(syms))


# ------------------------
# Exécution robuste SFI
# ------------------------
def _normalize_orders(orders: Union[None, dict, list, tuple]) -> List[Dict[str, Any]]:
    """Uniformise la sortie des méthodes d'exécution en liste de dicts."""
    if orders is None:
        return []
    if isinstance(orders, dict):
        return [orders]
    if isinstance(orders, list):
        # si la liste contient des tuples, convertis en dict minimal
        out: List[Dict[str, Any]] = []
        for it in orders:
            if isinstance(it, dict):
                out.append(it)
            elif isinstance(it, tuple):
                # on mappe génériquement
                d = {"raw": tuple(it)}
                out.append(d)
            else:
                out.append({"raw": it})
        return out
    if isinstance(orders, tuple):
        # évite l'erreur "'tuple' object has no attribute 'get'"
        return [{"raw": tuple(orders)}]
    # fallback
    return [{"raw": orders}]

def _maybe_configure_tranches(engine: SFIEngine, tp1: float, tp2: float) -> None:
    """Si l'engine supporte la config des tranches, configure une structure simple 2 TP."""
    try:
        if hasattr(engine, "configure_tranches") and callable(engine.configure_tranches):
            engine.configure_tranches([
                {"size": 0.5, "tp": float(tp1)},
                {"size": 0.5, "tp": float(tp2)},
            ])
    except Exception as e:
        log.debug("configure_tranches KO: %s", e)

def _safe_place_orders(engine: SFIEngine, entry: float, sl: float, tp1: float, tp2: float) -> List[Dict[str, Any]]:
    """Essaie plusieurs signatures usuelles des engines SFI, normalise la sortie."""
    _maybe_configure_tranches(engine, tp1, tp2)

    # 1) Signature la plus explicite (kwargs)
    try:
        orders = engine.place_initial(entry=float(entry), sl=float(sl), tp1=float(tp1), tp2=float(tp2))  # type: ignore
        return _normalize_orders(orders)
    except TypeError:
        pass
    except Exception as e:
        log.error("place_initial(kwargs) KO: %s", e)

    # 2) entry_hint
    try:
        orders = engine.place_initial(entry_hint=float(entry))  # type: ignore
        return _normalize_orders(orders)
    except TypeError:
        pass
    except Exception as e:
        log.error("place_initial(entry_hint) KO: %s", e)

    # 3) Positionnel (legacy)
    try:
        orders = engine.place_initial(float(entry), float(sl), float(tp1), float(tp2))  # type: ignore
        return _normalize_orders(orders)
    except TypeError:
        pass
    except Exception as e:
        log.error("place_initial(positional) KO: %s", e)

    # 4) Decision dict si exposé
    try:
        dec = {"entry": float(entry), "sl": float(sl), "tp1": float(tp1), "tp2": float(tp2)}
        if hasattr(engine, "place_from_decision") and callable(engine.place_from_decision):
            orders = engine.place_from_decision(dec)  # type: ignore
            return _normalize_orders(orders)
    except Exception as e:
        log.error("place_from_decision KO: %s", e)

    # 5) Dernier recours market si dispo
    try:
        if hasattr(engine, "place_market") and callable(engine.place_market):
            orders = engine.place_market()  # type: ignore
            return _normalize_orders(orders)
    except Exception as e:
        log.error("place_market KO: %s", e)

    return []


# ------------------------
# Event handler
# ------------------------
async def handle_symbol_event(ev: Dict[str, Any], rg: RiskGuard, policy: MetaPolicy):
    symbol = ev.get("symbol")
    etype  = ev.get("type")
    if etype != "bar":
        return
    log.info("event: %s", etype, extra={"symbol": symbol})

    # anti-spam analyse
    last = _LAST_ANALYSIS_TS.get(symbol, 0.0)
    if time.time() - last < ANALYSIS_MIN_INTERVAL_SEC:
        log.debug("skip: analysis throttle", extra={"symbol": symbol})
        return
    _LAST_ANALYSIS_TS[symbol] = time.time()

    # klines cache
    try:
        df_h1, df_h4 = _get_klines_cached(symbol)
    except Exception as e:
        log.warning("fetch_klines KO: %s", e, extra={"symbol": symbol})
        return
    if df_h1 is None and df_h4 is None:
        log.warning("klines vides", extra={"symbol": symbol})
        return

    # --- Institutionnel + autotune (si dispo)
    inst = {}
    if HAS_INST:
        try:
            inst = get_institutional_snapshot(symbol)
            log.info("inst: s=%.2f oi=%.2f d=%.2f f=%.2f liq=%.2f cvd=%d liq5m=%d",
                     float(inst.get("score",0)), float(inst.get("oi_score",0)),
                     float(inst.get("delta_score",0)), float(inst.get("funding_score",0)),
                     float(inst.get("liq_score",0)), int(inst.get("delta_cvd_usd",0)),
                     int(inst.get("liq_notional_5m",0)), extra={"symbol": symbol})
        except Exception as e:
            log.warning("inst snapshot KO: %s", e, extra={"symbol": symbol})
            inst = {}

    if HAS_INST and HAS_TUNER and TUNER is not None:
        try:
            thr = TUNER.update_and_get(symbol, df_h1, inst)  # seuils adaptatifs
            comps_cnt, comps_detail = components_ok(inst, thr)
            inst_ok = (float(inst.get("score",0)) >= thr["req_score"]) and (comps_cnt >= thr["components_min"])
            log.info("inst-gate: pass=%s score=%.2f req=%.2f comps=%d/%d q=%.2f atr%%=%.2f",
                     inst_ok, float(inst.get("score",0)), thr["req_score"], comps_cnt, thr["components_min"],
                     float(thr.get("q_used", 0.0)), float(thr.get("atr_pct", 0.0)), extra={"symbol": symbol})
            if not inst_ok:
                log.info("inst-reject details: %s", comps_detail, extra={"symbol": symbol})
                try: update_perf_for_symbol(symbol, df_h1=df_h1)
                except Exception: pass
                return
        except Exception as e:
            log.warning("autotune failed: %s", e, extra={"symbol": symbol})
            # on continue sans gating si l'autotune a un souci

    # --- Analyse (avec institutional si supporté)
    try:
        log.debug("analyze...", extra={"symbol": symbol})
        try:
            # nouvelle signature dict (valid=True/False) privilégiée
            res = analyze_signal.analyze_signal(symbol=symbol,
                                                entry_price=float(df_h1['close'].iloc[-1]),
                                                df_h1=df_h1, df_h4=df_h4,
                                                df_d1=df_h1, df_m15=df_h1,  # placeholders si non utilisés
                                                inst=inst, macro={})
        except TypeError:
            # signature plus simple
            res = analyze_signal.analyze_signal(symbol=symbol, df_h1=df_h1, df_h4=df_h4)
    except Exception as e:
        log.warning("analyze_signal KO: %s", e, extra={"symbol": symbol})
        return

    if not isinstance(res, dict):
        log.info("no-trade (bad result type)", extra={"symbol": symbol})
        try: update_perf_for_symbol(symbol, df_h1=df_h1)
        except Exception: pass
        return

    side = str(res.get("side", "none")).lower()
    rr   = float(res.get("rr", 0) or 0)
    score= float(res.get("inst_score", 0) or 0)
    comments_list = res.get("comments", []) or []
    comments = ", ".join([str(c) for c in comments_list]) if comments_list else ""
    log.info("analysis: side=%s rr=%.2f score=%.2f comment=%s",
             side, rr, score, comments or "—", extra={"symbol": symbol})

    if not res.get("valid", False):
        log.info("no-trade (invalid signal) — rr=%.2f score=%.2f reason=%s",
                 rr, score, res.get("reason", "no reason"), extra={"symbol": symbol})
        try: update_perf_for_symbol(symbol, df_h1=df_h1)
        except Exception: pass
        return

    # --- Risk guard
    rg_ok, rg_reason = rg.can_enter(symbol, ws_latency_ms=50, last_data_age_s=5)
    if not rg_ok:
        log.info("blocked by risk_guard: %s", rg_reason, extra={"symbol": symbol})
        return

    # --- Policy
    arm, weight, label = policy.choose({"atr_pct": res.get("atr_pct", 0), "adx_proxy": res.get("adx_proxy", 0)})
    if weight < 0.25 and rr < 1.5:
        log.info("policy reject — arm=%s w=%.2f rr=%.2f", arm, weight, rr, extra={"symbol": symbol})
        try: update_perf_for_symbol(symbol, df_h1=df_h1)
        except Exception: pass
        return

    # --- Exécution robuste
    entry = float(res.get("entry") or df_h1["close"].astype(float).iloc[-1])
    sl    = float(res.get("sl", 0.0) or 0.0)
    tp1   = float(res.get("tp1", 0.0) or 0.0)
    tp2   = float(res.get("tp2", 0.0) or 0.0)
    value_usdt = float(os.environ.get("ORDER_VALUE_USDT", "20"))

    log.info("EXEC %s entry=%s sl=%s tp1=%s tp2=%s val=%s",
             side.upper(), fmt_price(entry), fmt_price(sl), fmt_price(tp1), fmt_price(tp2), value_usdt,
             extra={"symbol": symbol})

    try:
        eng = SFIEngine(symbol, side, value_usdt, sl, tp1, tp2)
    except TypeError:
        # certaines versions attendent (symbol, side, config={...})
        eng = SFIEngine(symbol, side, {"notional": value_usdt, "sl": sl, "tp1": tp1, "tp2": tp2})

    orders = _safe_place_orders(eng, entry, sl, tp1, tp2)
    log.info("orders=%s", orders, extra={"symbol": symbol})

    # Telegram
    msg = (f"🧠 *{symbol}* — *{side.upper()}* via *{label}*\n"
           f"RR: *{res.get('rr','—')}*  |  Entrée: *{fmt_price(entry)}*  |  SL: *{fmt_price(sl)}*  |  TP1: *{fmt_price(tp1)}*  TP2: *{fmt_price(tp2)}*\n"
           f"Ordres: {orders if orders else '—'}")
    send_telegram(msg)

    key = f"{symbol}:{side}:{fmt_price(entry)}:{round(res.get('rr',0),2)}"
    register_signal_perf(key, symbol, side, entry)
    try: update_perf_for_symbol(symbol, df_h1=df_h1)
    except Exception: pass


# ------------------------
# Main
# ------------------------
async def main():
    init_logging()
    if os.getenv("LOG_HTTP", "0") == "1":
        enable_httpx(True)

    logging.getLogger("runner").info("start")

    # Build des symboles contrats via meta (ou via env SYMBOLS)
    try:
        symbols = _build_symbols()
    except Exception as e:
        logging.getLogger("runner").error("Build symbols KO: %s", e)
        symbols = []

    if not symbols:
        logging.getLogger("runner").warning("Aucun symbole.")
        return

    bus = EventBus()
    src = PollingSource(symbols, interval_sec=WS_POLL_SEC)
    bus.add_source(src.__aiter__())
    await bus.start()

    rg = RiskGuard()
    policy = MetaPolicy()

    async for ev in bus.events():
        try:
            await handle_symbol_event(ev, rg, policy)
        except Exception as e:
            logging.getLogger("runner").error("handle_symbol_event: %s", e)

if __name__ == "__main__":
    asyncio.run(main())
