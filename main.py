# -*- coding: utf-8 -*-
"""
main.py — Boucle event-driven + fallback institutionnel structuré (OTE, liquidité, swings)
- Direction H4, exécution H1 via OTE 62–79% et pools de liquidité
- SL derrière la liquidité/swing + buffer ATR
- TP1 swing/pool opposé, TP2 RR cible (2.0 par défaut)
"""

import os, asyncio, logging, math, time
from typing import Dict, Any, Tuple, List, Union

from ws_router import EventBus, PollingSource
from execution_sfi import SFIEngine
from risk_guard import RiskGuard
from meta_policy import MetaPolicy
from perf_metrics import register_signal_perf, update_perf_for_symbol
from kucoin_utils import fetch_klines, fetch_symbol_meta
from log_setup import init_logging, enable_httpx

# ---- Soft imports institutionnel / autotune
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

H1_LIMIT                  = int(os.getenv("H1_LIMIT", "500"))
H4_LIMIT                  = int(os.getenv("H4_LIMIT", "400"))
H1_REFRESH_SEC            = int(os.getenv("H1_REFRESH_SEC", "60"))
H4_REFRESH_SEC            = int(os.getenv("H4_REFRESH_SEC", "300"))
ANALYSIS_MIN_INTERVAL_SEC = int(os.getenv("ANALYSIS_MIN_INTERVAL_SEC", "15"))
WS_POLL_SEC               = int(os.getenv("WS_POLL_SEC", "5"))

# Cibles / buffers institutionnels
RR_TARGET_TP2             = float(os.getenv("INST_RR_TARGET_TP2", "2.0"))
ATR_SL_MULT               = float(os.getenv("INST_ATR_SL_MULT", "1.0"))     # buffer ajouté derrière le swing/liquidité
ATR_MIN_PCT               = float(os.getenv("INST_ATR_MIN_PCT", "0.003"))   # fallback ATR min = 0.3% prix
EQ_TOL_PCT                = float(os.getenv("INST_EQ_TOL_PCT", "0.0006"))   # tolérance equal highs/lows (0.06%)
OTE_LOW                   = float(os.getenv("INST_OTE_LOW", "0.62"))
OTE_HIGH                  = float(os.getenv("INST_OTE_HIGH", "0.79"))
OTE_MID                   = (OTE_LOW + OTE_HIGH) / 2.0

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
    import httpx
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = {"chat_id": TELEGRAM_CHAT_ID, "text": text, "parse_mode":"Markdown", "disable_web_page_preview": True}
    for attempt in (1, 2):
        try:
            resp = httpx.post(url, json=payload, timeout=10)
            if resp.status_code == 200 and (resp.json().get("ok") is True):
                log.info("Telegram OK (len=%s)", len(text)); return
            else:
                log.warning("Telegram HTTP=%s body=%s (attempt %s)", resp.status_code, resp.text[:200], attempt)
        except Exception as e:
            log.error("Telegram KO: %s (attempt %s)", e, attempt)

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
    env_syms = os.getenv("SYMBOLS", "").strip()
    if env_syms:
        lst = [s.strip().upper() for s in env_syms.split(",") if s.strip()]
        return sorted(set(lst))
    meta = fetch_symbol_meta()
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
    if orders is None:
        return []
    if isinstance(orders, dict):
        return [orders]
    if isinstance(orders, list):
        out: List[Dict[str, Any]] = []
        for it in orders:
            if isinstance(it, dict):
                out.append(it)
            elif isinstance(it, tuple):
                out.append({"raw": tuple(it)})
            else:
                out.append({"raw": it})
        return out
    if isinstance(orders, tuple):
        return [{"raw": tuple(orders)}]
    return [{"raw": orders}]

def _maybe_configure_tranches(engine: SFIEngine, tp1: float, tp2: float) -> None:
    try:
        if hasattr(engine, "configure_tranches") and callable(engine.configure_tranches):
            engine.configure_tranches([
                {"size": 0.5, "tp": float(tp1)},
                {"size": 0.5, "tp": float(tp2)},
            ])
    except Exception as e:
        log.debug("configure_tranches KO: %s", e)

def _safe_place_orders(engine: SFIEngine, entry: float, sl: float, tp1: float, tp2: float) -> List[Dict[str, Any]]:
    _maybe_configure_tranches(engine, tp1, tp2)
    try:
        orders = engine.place_initial(entry=float(entry), sl=float(sl), tp1=float(tp1), tp2=float(tp2))  # type: ignore
        return _normalize_orders(orders)
    except TypeError:
        pass
    except Exception as e:
        log.error("place_initial(kwargs) KO: %s", e)
    try:
        orders = engine.place_initial(entry_hint=float(entry))  # type: ignore
        return _normalize_orders(orders)
    except TypeError:
        pass
    except Exception as e:
        log.error("place_initial(entry_hint) KO: %s", e)
    try:
        orders = engine.place_initial(float(entry), float(sl), float(tp1), float(tp2))  # type: ignore
        return _normalize_orders(orders)
    except TypeError:
        pass
    except Exception as e:
        log.error("place_initial(positional) KO: %s", e)
    try:
        dec = {"entry": float(entry), "sl": float(sl), "tp1": float(tp1), "tp2": float(tp2)}
        if hasattr(engine, "place_from_decision") and callable(engine.place_from_decision):
            orders = engine.place_from_decision(dec)  # type: ignore
            return _normalize_orders(orders)
    except Exception as e:
        log.error("place_from_decision KO: %s", e)
    try:
        if hasattr(engine, "place_market") and callable(engine.place_market):
            orders = engine.place_market()  # type: ignore
            return _normalize_orders(orders)
    except Exception as e:
        log.error("place_market KO: %s", e)
    return []

# ------------------------
# Outils de structure "institutionnels"
# ------------------------
def _compute_atr(df, period: int = 14) -> float:
    try:
        h = df['high'].astype(float); l = df['low'].astype(float); c = df['close'].astype(float)
        prev_c = c.shift(1)
        tr = (h - l).abs()
        tr = tr.combine((h - prev_c).abs(), max).combine((l - prev_c).abs(), max)
        atr = tr.rolling(window=period, min_periods=period).mean().iloc[-1]
        return float(atr) if atr and atr > 0 else 0.0
    except Exception:
        return 0.0

def _swing_highs_lows(df, lookback: int = 3) -> Tuple[List[int], List[int]]:
    """
    Détection simple: swing high si High[i] est le max sur i-lookback..i+lookback.
    Retourne index des swing_highs et swing_lows.
    """
    hs, ls = [], []
    h = df['high'].astype(float).values
    l = df['low'].astype(float).values
    n = len(df)
    for i in range(lookback, n - lookback):
        if h[i] == max(h[i-lookback:i+lookback+1]):
            hs.append(i)
        if l[i] == min(l[i-lookback:i+lookback+1]):
            ls.append(i)
    return hs, ls

def _last_impulse(df, side_hint: str) -> Tuple[float, float]:
    """
    Trouve une jambe impulsive récente:
     - LONG: dernier swing low -> dernier swing high plus récent
     - SHORT: dernier swing high -> dernier swing low plus récent
    """
    hs, ls = _swing_highs_lows(df, lookback=3)
    if not hs or not ls:
        c = float(df['close'].astype(float).iloc[-1])
        return (c * 0.98, c * 1.02) if side_hint == "long" else (c * 1.02, c * 0.98)
    if side_hint == "long":
        last_low_idx = ls[-1]
        later_highs = [i for i in hs if i > last_low_idx]
        if later_highs:
            hh = float(df['high'].astype(float).iloc[later_highs[-1]])
            ll = float(df['low'].astype(float).iloc[last_low_idx])
            return ll, hh
    else:
        last_high_idx = hs[-1]
        later_lows = [i for i in ls if i > last_high_idx]
        if later_lows:
            ll = float(df['low'].astype(float).iloc[later_lows[-1]])
            hh = float(df['high'].astype(float).iloc[last_high_idx])
            return hh, ll
    # fallback: borne min/max sur 100 dernières barres
    win = df.tail(100)
    return float(win['low'].min()), float(win['high'].max())

def _equal_levels(prices: List[float], tol_pct: float) -> List[float]:
    """
    Regroupe des niveaux “égaux” dans une tolérance en %.
    Retourne la liste des niveaux (médianes de clusters).
    """
    if not prices: return []
    prices = sorted(prices)
    clusters = [[prices[0]]]
    for p in prices[1:]:
        if abs(p - clusters[-1][-1]) / clusters[-1][-1] <= tol_pct:
            clusters[-1].append(p)
        else:
            clusters.append([p])
    # ne garder que les pools significatifs (>=2 touches)
    pools = [sum(c)/len(c) for c in clusters if len(c) >= 2]
    return pools

def _liquidity_pools(df) -> Tuple[List[float], List[float]]:
    """Pools de liquidité simples via equal highs / equal lows sur H1."""
    hs, ls = _swing_highs_lows(df, lookback=2)
    highs = [float(df['high'].iloc[i]) for i in hs]
    lows  = [float(df['low'].iloc[i])  for i in ls]
    return _equal_levels(highs, EQ_TOL_PCT), _equal_levels(lows, EQ_TOL_PCT)

def _h4_direction(df_h4, inst: Dict[str, Any]) -> str:
    """
    Direction “institutionnelle”:
      - BOS simplifié via HH/LL récents + CVD/Funding/Delta pour pondérer.
    """
    hs, ls = _swing_highs_lows(df_h4, lookback=2)
    dir_struct = "long"
    if len(hs) >= 2 and len(ls) >= 2:
        hh_new = df_h4['high'].iloc[hs[-1]] > df_h4['high'].iloc[hs[-2]]
        ll_new = df_h4['low'].iloc[ls[-1]]  < df_h4['low'].iloc[ls[-2]]
        if hh_new and not ll_new:
            dir_struct = "long"
        elif ll_new and not hh_new:
            dir_struct = "short"
        else:
            # neutre → pondérer par insti
            pass
    cvd = float(inst.get("delta_cvd_usd", 0) or 0.0)
    d_s = float(inst.get("delta_score", 0) or 0.0)
    f_s = float(inst.get("funding_score", 0) or 0.0)
    bias = "long" if (cvd > 0 or (d_s >= 0.5 and f_s >= 0.6)) else "short"
    # Si conflictuel, garder struct, sinon suivre bias
    return dir_struct if dir_struct != "neutral" else bias

def _project_ote_entry(ll: float, hh: float, side: str) -> float:
    if side == "long":
        # retracement depuis HH vers LL (Fib down)
        return hh - (hh - ll) * OTE_MID
    else:
        # retracement depuis LL vers HH (Fib up)
        return ll + (hh - ll) * OTE_MID

def _inst_structured_decision(symbol: str, inst: Dict[str, Any], df_h1, df_h4) -> Union[None, Dict[str, Any]]:
    """
    Décision fallback institutionnelle structurée:
      - Direction H4 (BOS simplifié) + insti
      - Impulsion H1 -> OTE 62–79%
      - SL derrière pool de liquidité / swing + buffer ATR
      - TP1 swing/pool opposé, TP2 RR cible
    """
    try:
        side = _h4_direction(df_h4, inst)
        # Jambe impulsive (H1)
        ll, hh = _last_impulse(df_h1, side)
        entry = _project_ote_entry(ll, hh, side)

        atr = _compute_atr(df_h1, period=14)
        if atr <= 0:
            atr = float(df_h1['close'].astype(float).iloc[-1]) * ATR_MIN_PCT

        # Pools de liquidité (H1)
        eq_highs, eq_lows = _liquidity_pools(df_h1)

        if side == "long":
            # SL sous le pool de lows le plus proche sous le swing low, sinon sous le swing low
            pool_below = [p for p in eq_lows if p <= ll * (1 + EQ_TOL_PCT)]
            sl_base = max(pool_below) if pool_below else ll
            sl = max(1e-12, sl_base - ATR_SL_MULT * atr)
            # TP1 vers le dernier HH/pool
            tp1_candidate = max(eq_highs) if eq_highs else hh
            tp1 = max(entry + atr, tp1_candidate)  # au moins +1*ATR, sinon pool
            # TP2 par RR cible
            risk = max(entry - sl, 1e-12)
            tp2 = entry + RR_TARGET_TP2 * risk
        else:
            pool_above = [p for p in eq_highs if p >= hh * (1 - EQ_TOL_PCT)]
            sl_base = min(pool_above) if pool_above else hh
            sl = sl_base + ATR_SL_MULT * atr
            tp1_candidate = min(eq_lows) if eq_lows else ll
            tp1 = min(entry - atr, tp1_candidate)  # au moins -1*ATR, sinon pool
            risk = max(sl - entry, 1e-12)
            tp2 = entry - RR_TARGET_TP2 * risk

        rr = abs((tp2 - entry) / (entry - sl))

        return {
            "valid": True,
            "side": side,
            "entry": float(entry),
            "sl": float(sl),
            "tp1": float(tp1),
            "tp2": float(tp2),
            "rr": float(rr),
            "reason": "INST_STRUCT_FALLBACK",
            "comments": ["inst_struct", f"atr={atr:.8f}", f"impulse=({ll:.10f},{hh:.10f})"]
        }
    except Exception as e:
        log.warning("inst_structured_decision KO: %s", e, extra={"symbol": symbol})
        return None

# ------------------------
# Event handler
# ------------------------
async def handle_symbol_event(ev: Dict[str, Any], rg: RiskGuard, policy: MetaPolicy):
    symbol = ev.get("symbol")
    etype  = ev.get("type")
    if etype != "bar":
        return
    log.info("event: %s", etype, extra={"symbol": symbol})

    # anti-spam
    last = _LAST_ANALYSIS_TS.get(symbol, 0.0)
    if time.time() - last < ANALYSIS_MIN_INTERVAL_SEC:
        log.debug("skip: analysis throttle", extra={"symbol": symbol})
        return
    _LAST_ANALYSIS_TS[symbol] = time.time()

    # klines
    try:
        df_h1, df_h4 = _get_klines_cached(symbol)
    except Exception as e:
        log.warning("fetch_klines KO: %s", e, extra={"symbol": symbol})
        return
    if df_h1 is None and df_h4 is None:
        log.warning("klines vides", extra={"symbol": symbol})
        return

    # --- Institutionnel + autotune
    inst: Dict[str, Any] = {}
    inst_gate_pass = True
    inst_gate_reason = "n/a"
    comps_cnt = 0
    comps_min = 0
    thr = {}

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
            thr = TUNER.update_and_get(symbol, df_h1, inst)
            comps_cnt, comps_detail = components_ok(inst, thr)
            comps_min = thr["components_min"]

            score_val = float(inst.get("score", 0) or 0.0)
            req_score = float(thr.get("req_score", 1.2) or 1.2)

            if comps_cnt >= 4:
                inst_gate_pass = True;  inst_gate_reason = "force_pass_4of4"
            elif comps_cnt >= 3:
                inst_gate_pass = True;  inst_gate_reason = "tolerance_pass_3of4"
            elif score_val >= req_score:
                inst_gate_pass = True;  inst_gate_reason = "score_gate"
            else:
                inst_gate_pass = False; inst_gate_reason = "reject"

            log.info("inst-gate: pass=%s reason=%s score=%.2f req=%.2f comps=%d/%d q=%.2f atr%%=%.2f",
                     inst_gate_pass, inst_gate_reason, score_val, req_score, comps_cnt, comps_min,
                     float(thr.get("q_used", 0.0)), float(thr.get("atr_pct", 0.0)), extra={"symbol": symbol})

            if not inst_gate_pass:
                log.info("inst-reject details: %s", comps_detail, extra={"symbol": symbol})
                try: update_perf_for_symbol(symbol, df_h1=df_h1)
                except Exception: pass
                return
        except Exception as e:
            log.warning("autotune failed: %s", e, extra={"symbol": symbol})
            inst_gate_pass = True
            inst_gate_reason = "autotune_fail_bypass"

    # --- Analyse “classique”
    try:
        log.debug("analyze...", extra={"symbol": symbol})
        try:
            res = analyze_signal.analyze_signal(
                symbol=symbol,
                entry_price=float(df_h1['close'].iloc[-1]),
                df_h1=df_h1, df_h4=df_h4,
                df_d1=df_h1, df_m15=df_h1,
                inst=inst, macro={}
            )
        except TypeError:
            res = analyze_signal.analyze_signal(symbol=symbol, df_h1=df_h1, df_h4=df_h4)
    except Exception as e:
        log.warning("analyze_signal KO: %s", e, extra={"symbol": symbol})
        return

    if not isinstance(res, dict):
        log.info("no-trade (bad result type)", extra={"symbol": symbol})
        try: update_perf_for_symbol(symbol, df_h1=df_h1)
        except Exception: pass
        return

    side   = str(res.get("side", "none")).lower()
    rr     = float(res.get("rr", 0) or 0)
    score  = float(res.get("inst_score", 0) or 0)
    c_list = res.get("comments", []) or []
    comments = ", ".join([str(c) for c in c_list]) if c_list else ""
    log.info("analysis: side=%s rr=%.2f score=%.2f comment=%s",
             side, rr, score, comments or "—", extra={"symbol": symbol})

    diag = (res.get("manage", {}) or {}).get("diagnostics", {})
    tolerated = diag.get("tolerated", res.get("tolerated", []))

    # --- Fallback institutionnel STRUCTURÉ si signal invalide et gate OK
    force_exec = False
    if not res.get("valid", False) or side not in ("long", "short"):
        reason = res.get("reason") or "REJET — analyse classique invalide"
        log.info("no-trade (invalid signal) — rr=%.2f score=%.2f reason=%s tolerated=%s diag=%s",
                 rr, score, reason, tolerated, diag, extra={"symbol": symbol})

        if HAS_INST and inst_gate_pass:
            fb = _inst_structured_decision(symbol, inst, df_h1, df_h4)
            if fb:
                res.update(fb)
                side = fb["side"]; rr = float(fb["rr"])
                log.info("INST_STRUCT_FALLBACK applied — side=%s rr=%.2f entry=%s sl=%s tp1=%s tp2=%s",
                         side, rr, fmt_price(fb["entry"]), fmt_price(fb["sl"]),
                         fmt_price(fb["tp1"]), fmt_price(fb["tp2"]), extra={"symbol": symbol})
                force_exec = True
            else:
                try: update_perf_for_symbol(symbol, df_h1=df_h1)
                except Exception: pass
                return
        else:
            try: update_perf_for_symbol(symbol, df_h1=df_h1)
            except Exception: pass
            return

    # --- Risk guard
    rg_ok, rg_reason = rg.can_enter(symbol, ws_latency_ms=50, last_data_age_s=5)
    if not rg_ok:
        log.info("blocked by risk_guard: %s", rg_reason, extra={"symbol": symbol})
        return

    # --- Policy (bypass si fallback insti)
    label = "INST_STRUCT" if force_exec else None
    if not force_exec:
        arm, weight, label = policy.choose({"atr_pct": res.get("atr_pct", 0), "adx_proxy": res.get("adx_proxy", 0)})
        if weight < 0.25 and rr < 1.5:
            log.info("policy reject — arm=%s w=%.2f rr=%.2f", arm, weight, rr, extra={"symbol": symbol})
            try: update_perf_for_symbol(symbol, df_h1=df_h1)
            except Exception: pass
            return

    # --- Exécution
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
        eng = SFIEngine(symbol, side, {"notional": value_usdt, "sl": sl, "tp1": tp1, "tp2": tp2})

    orders = _safe_place_orders(eng, entry, sl, tp1, tp2)
    log.info("orders=%s", orders, extra={"symbol": symbol})

    # Telegram
    lbl = label or "META_POLICY"
    msg = (f"🧠 *{symbol}* — *{side.upper()}* via *{lbl}*\n"
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
