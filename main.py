# -*- coding: utf-8 -*-
"""
main.py — Boucle event-driven + fallback institutionnel structuré (OTE, liquidité, swings)
- Direction H4, exécution H1 via OTE 62–79% et pools de liquidité
- SL derrière la liquidité/swing + buffer ATR
- TP1 swing/pool opposé, TP2 RR cible (2.0 par défaut)
- Exécution SFI + fallback direct KuCoin (sans poll inutile sur échec POST)
- 🔎 Logs enrichis: erreurs détaillées, raisons de blocage, score institutionnel + composants
"""

# ===== UNIVERSAL IMPORT FIX (sans __init__.py, compatible Railway) =====
import sys, os, pathlib
HERE = pathlib.Path(__file__).resolve().parent
CANDIDATES = [
    HERE,
    HERE.parent,
    pathlib.Path("/app"),          # Railway
    pathlib.Path("/workspace"),    # Replit / dev local
]
for base in CANDIDATES:
    if (base / "core").exists() or (base / "ws_router.py").exists():
        if str(base) not in sys.path:
            sys.path.insert(0, str(base))
        break
# =====================================================================

import os, asyncio, logging, math, time
from typing import Dict, Any, Tuple, List, Union

# --- ws_router (core ou racine)
try:
    from core.ws_router import EventBus, PollingSource
except Exception:
    from ws_router import EventBus, PollingSource  # type: ignore

# --- SFI engine
try:
    from execution_sfi import SFIEngine  # racine
except Exception:
    try:
        from core.execution_sfi import SFIEngine  # si déplacé
    except Exception:
        from execution.execution_sfi import SFIEngine  # autre structure

# --- RiskGuard
try:
    from risk_guard import RiskGuard  # racine
except Exception:
    try:
        from core.risk_guard import RiskGuard
    except Exception:
        from risk.risk_guard import RiskGuard

# --- MetaPolicy
try:
    from meta_policy import MetaPolicy
except Exception:
    try:
        from core.meta_policy import MetaPolicy
    except Exception:
        from policy.meta_policy import MetaPolicy

# --- Metrics
try:
    from perf_metrics import register_signal_perf, update_perf_for_symbol
except Exception:
    def register_signal_perf(*a, **k): pass
    def update_perf_for_symbol(*a, **k): pass

# --- KuCoin utils/adapters (fetch klines + meta)
# fetch_klines: tente d'abord kucoin_utils, sinon kucoin_adapter
_fetch_klines = None
try:
    from kucoin_utils import fetch_klines as _fetch_klines  # type: ignore
except Exception:
    try:
        from kucoin_adapter import fetch_klines as _fetch_klines  # type: ignore
    except Exception:
        _fetch_klines = None

# symbol meta
try:
    from kucoin_utils import fetch_symbol_meta  # type: ignore
except Exception:
    def fetch_symbol_meta():
        return {}

# log setup (init_logging, enable_httpx)
try:
    from log_setup import init_logging, enable_httpx  # racine
except Exception:
    try:
        from core.log_setup import init_logging, enable_httpx  # si packagé
    except Exception:
        # fallback no-op
        def init_logging(): logging.basicConfig(level=logging.INFO)
        def enable_httpx(_): pass

# KuCoin adapter (place orders + meta + positionMode)
try:
    from kucoin_adapter import (
        place_limit_order,
        get_symbol_meta,
        get_server_position_mode,  # ✅ pré-check du mode serveur
    )
except Exception:
    # fallback strict si module renommé
    from exchanges.kucoin_adapter import (
        place_limit_order,
        get_symbol_meta,
        get_server_position_mode,
    )

# get_order_by_client_oid est optionnel : pas d'ImportError si absent
try:
    from kucoin_adapter import get_order_by_client_oid  # type: ignore
except Exception:
    get_order_by_client_oid = None  # type: ignore

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
ATR_SL_MULT               = float(os.getenv("INST_ATR_SL_MULT", "1.0"))
ATR_MIN_PCT               = float(os.getenv("INST_ATR_MIN_PCT", "0.003"))
EQ_TOL_PCT                = float(os.getenv("INST_EQ_TOL_PCT", "0.0006"))
OTE_LOW                   = float(os.getenv("INST_OTE_LOW", "0.62"))
OTE_HIGH                  = float(os.getenv("INST_OTE_HIGH", "0.79"))
OTE_MID                   = (OTE_LOW + OTE_HIGH) / 2.0

# Fallback KuCoin
KC_POST_ONLY_DEFAULT      = os.getenv("KC_POST_ONLY", "1") == "1"
KC_VERIFY_MAX_TRIES       = int(os.getenv("KC_VERIFY_MAX_TRIES", "5"))
KC_VERIFY_DELAY_SEC       = float(os.getenv("KC_VERIFY_DELAY_SEC", "0.35"))

_KLINE_CACHE: Dict[str, Dict[str, Any]] = {}
_LAST_ANALYSIS_TS: Dict[str, float] = {}

log = logging.getLogger("runner")
TUNER = InstAutoTune() if HAS_TUNER else None  # type: ignore

# ------------------------
# Utils
# ------------------------
def _log_block(stage: str, msg: str, symbol: Union[str, None] = None, **fields):
    """Log standardisé avec stage + champs utiles."""
    extra = {"stage": stage, "symbol": symbol} if symbol else {"stage": stage}
    try:
        kv = " ".join(f"{k}={v}" for k, v in fields.items())
        log.info(f"[{stage}] {msg} {kv}".strip(), extra=extra)
    except Exception:
        log.info(f"[{stage}] {msg}", extra=extra)

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

def _fetch_klines_safe(symbol: str, interval: str, limit: int):
    """Wrapper qui cherche fetch_klines sur kucoin_utils puis kucoin_adapter; sinon erreur claire."""
    if callable(_fetch_klines):
        return _fetch_klines(symbol, interval=interval, limit=limit)  # type: ignore
    raise RuntimeError("Aucun fetch_klines disponible (kucoin_utils/kucoin_adapter introuvables)")

def _get_klines_cached(symbol: str) -> Tuple[Any, Any]:
    now = time.time()
    ent = _KLINE_CACHE.get(symbol, {})
    need_h1 = ("h1" not in ent) or (now - ent.get("ts_h1", 0) > H1_REFRESH_SEC)
    need_h4 = ("h4" not in ent) or (now - ent.get("ts_h4", 0) > H4_REFRESH_SEC)

    if need_h1:
        ent["h1"] = _fetch_klines_safe(symbol, interval="1h", limit=H1_LIMIT)
        ent["ts_h1"] = now
        log.debug("H1 fetch", extra={"symbol": symbol})
    else:
        log.debug("H1 cache hit", extra={"symbol": symbol})

    if need_h4:
        ent["h4"] = _fetch_klines_safe(symbol, interval="4h", limit=H4_LIMIT)
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
    if isinstance(meta, dict):
        for v in meta.values():
            sym_api = str(v.get("symbol_api", "")).strip().upper()
            if sym_api.endswith("USDTM"):
                syms.append(sym_api)
    return sorted(set(syms))

def _round_to_tick(px: float, tick: float) -> float:
    if not tick or tick <= 0:
        return float(px)
    return math.floor(float(px) / float(tick)) * float(tick)

def _side_to_adapter(side: str) -> str:
    s = (side or "").lower()
    if s in ("buy", "sell"):
        return s
    return "buy" if s == "long" else "sell"

def _has_real_order_id(orders: List[Dict[str, Any]]) -> bool:
    for o in orders or []:
        if not isinstance(o, dict):
            continue
        if o.get("orderId"):
            return True
        code = str(o.get("code", "")).strip()
        if o.get("ok") is True and code in ("200000",):
            if o.get("orderId") or o.get("clientOid"):
                return True
    return False

# ------------------------
# Exécution robuste SFI
# ------------------------
def _normalize_orders(orders: Union[None, dict, list, tuple, str]) -> List[Dict[str, Any]]:
    def _from_str(s: str) -> Dict[str, Any]:
        s = str(s).strip()
        if not s:
            return {"raw": s}
        if (len(s) in (32, 36)) or s.isalnum():
            return {"ok": True, "clientOid": s, "raw": s}
        return {"raw": s}

    out: List[Dict[str, Any]] = []
    if orders is None:
        return out

    if isinstance(orders, dict):
        d = dict(orders)
        data = d.get("data")
        if isinstance(data, dict):
            if "orderId" in data and "orderId" not in d:
                d["orderId"] = data.get("orderId")
            if "clientOid" in data and "clientOid" not in d:
                d["clientOid"] = data.get("clientOid")
        out.append(d)
        return out

    if isinstance(orders, str):
        out.append(_from_str(orders))
        return out

    if isinstance(orders, tuple):
        out.append({"raw": tuple(orders)})
        return out

    if isinstance(orders, list):
        for it in orders:
            if isinstance(it, dict):
                d = dict(it)
                data = d.get("data")
                if isinstance(data, dict):
                    if "orderId" in data and "orderId" not in d:
                        d["orderId"] = data.get("orderId")
                    if "clientOid" in data and "clientOid" not in d:
                        d["clientOid"] = data.get("clientOid")
                out.append(d)
            elif isinstance(it, str):
                out.append(_from_str(it))
            elif isinstance(it, tuple):
                out.append({"raw": tuple(it)})
            else:
                out.append({"raw": it})
        return out

    return [{"raw": orders}]

def _maybe_configure_tranches(engine: "SFIEngine", tp1: float, tp2: float) -> None:
    try:
        if not hasattr(engine, "configure_tranches") or not callable(engine.configure_tranches):
            return
        try:
            engine.configure_tranches([
                (0.5, float(tp1)),
                (0.5, float(tp2)),
            ])
            return
        except Exception as e:
            _log_block("SFI", f"configure_tranches(tuple) KO: {e}")
        try:
            engine.configure_tranches([
                {"size": 0.5, "tp": float(tp1)},
                {"size": 0.5, "tp": float(tp2)},
            ])
            return
        except Exception as e:
            _log_block("SFI", f"configure_tranches(dict) KO: {e}")
        try:
            engine.configure_tranches([float(tp1), float(tp2)])
            return
        except Exception as e:
            _log_block("SFI", f"configure_tranches(list) KO: {e}")
    except Exception as e:
        _log_block("SFI", f"configure_tranches wrapper KO: {e}")

def _safe_place_orders(engine: "SFIEngine", entry: float, sl: float, tp1: float, tp2: float) -> List[Dict[str, Any]]:
    _maybe_configure_tranches(engine, tp1, tp2)
    try:
        if hasattr(engine, "open_limit") and callable(engine.open_limit):
            _log_block("SFI", "try open_limit(entry, sl, tp1, tp2)")
            orders = engine.open_limit(float(entry), float(sl), float(tp1), float(tp2))  # type: ignore
            return _normalize_orders(orders)
    except Exception as e:
        _log_block("SFI", f"open_limit KO: {e}")
    try:
        _log_block("SFI", "try place_initial kwargs")
        orders = engine.place_initial(entry=float(entry), sl=float(sl), tp1=float(tp1), tp2=float(tp2))  # type: ignore
        return _normalize_orders(orders)
    except TypeError:
        pass
    except Exception as e:
        _log_block("SFI", f"place_initial(kwargs) KO: {e}")
    try:
        _log_block("SFI", "try place_initial(entry_hint)")
        orders = engine.place_initial(entry_hint=float(entry))  # type: ignore
        return _normalize_orders(orders)
    except TypeError:
        pass
    except Exception as e:
        _log_block("SFI", f"place_initial(entry_hint) KO: {e}")
    try:
        _log_block("SFI", "try place_initial positional")
        orders = engine.place_initial(float(entry), float(sl), float(tp1), float(tp2))  # type: ignore
        return _normalize_orders(orders)
    except TypeError:
        pass
    except Exception as e:
        _log_block("SFI", f"place_initial(positional) KO: {e}")
    try:
        if hasattr(engine, "place_from_decision") and callable(engine.place_from_decision):
            _log_block("SFI", "try place_from_decision")
            dec = {"entry": float(entry), "sl": float(sl), "tp1": float(tp1), "tp2": float(tp2)}
            orders = engine.place_from_decision(dec)  # type: ignore
            return _normalize_orders(orders)
    except Exception as e:
        _log_block("SFI", f"place_from_decision KO: {e}")
    try:
        if hasattr(engine, "place_market") and callable(engine.place_market):
            _log_block("SFI", "try place_market")
            orders = engine.place_market()  # type: ignore
            return _normalize_orders(orders)
    except Exception as e:
        _log_block("SFI", f"place_market KO: {e}")
    _log_block("SFI", "Aucune méthode d'exécution n'a abouti (orders vides)")
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
    hs, ls = _swing_highs_lows(df, lookback=3)
    if not hs or not ls:
        c = float(df['close'].astype(float).iloc[-1])
        return (c * 0.98, c * 1.02) if side_hint == "long" else (c * 1.02, c * 0.98)
    if side_hint == "long":
        last_low_idx = ls[-1]
        later_highs = [i for i in hs if i > last_low_idx]
        if later_highs:
            hh = float(df['high'].astype(float).iloc[later_highs[-1]]); ll = float(df['low'].astype(float).iloc[last_low_idx])
            return ll, hh
    else:
        last_high_idx = hs[-1]
        later_lows = [i for i in ls if i > last_high_idx]
        if later_lows:
            ll = float(df['low'].astype(float).iloc[later_lows[-1]]); hh = float(df['high'].astype(float).iloc[last_high_idx])
            return hh, ll
    win = df.tail(100)
    return float(win['low'].min()), float(win['high'].max())

def _equal_levels(prices: List[float], tol_pct: float) -> List[float]:
    if not prices: return []
    prices = sorted(prices)
    clusters = [[prices[0]]]
    for p in prices[1:]:
        if abs(p - clusters[-1][-1]) / clusters[-1][-1] <= tol_pct:
            clusters[-1].append(p)
        else:
            clusters.append([p])
    pools = [sum(c)/len(c) for c in clusters if len(c) >= 2]
    return pools

def _liquidity_pools(df) -> Tuple[List[float], List[float]]:
    hs, ls = _swing_highs_lows(df, lookback=2)
    highs = [float(df['high'].iloc[i]) for i in hs]
    lows  = [float(df['low'].iloc[i])  for i in ls]
    return _equal_levels(highs, EQ_TOL_PCT), _equal_levels(lows, EQ_TOL_PCT)

def _h4_direction(df_h4, inst: Dict[str, Any]) -> str:
    hs, ls = _swing_highs_lows(df_h4, lookback=2)
    dir_struct = "long"
    if len(hs) >= 2 and len(ls) >= 2:
        hh_new = df_h4['high'].iloc[hs[-1]] > df_h4['high'].iloc[hs[-2]]
        ll_new = df_h4['low'].iloc[ls[-1]]  < df_h4['low'].iloc[ls[-2]]
        if hh_new and not ll_new:
            dir_struct = "long"
        elif ll_new and not hh_new:
            dir_struct = "short"
    cvd = float(inst.get("delta_cvd_usd", 0) or 0.0)
    d_s = float(inst.get("delta_score", 0) or 0.0)
    f_s = float(inst.get("funding_score", 0) or 0.0)
    bias = "long" if (cvd > 0 or (d_s >= 0.5 and f_s >= 0.6)) else "short"
    return dir_struct if dir_struct != "neutral" else bias

def _project_ote_entry(ll: float, hh: float, side: str) -> float:
    if side == "long":
        return hh - (hh - ll) * OTE_MID
    else:
        return ll + (hh - ll) * OTE_MID

def _inst_structured_decision(symbol: str, inst: Dict[str, Any], df_h1, df_h4) -> Union[None, Dict[str, Any]]:
    try:
        side = _h4_direction(df_h4, inst)
        ll, hh = _last_impulse(df_h1, side)
        entry = _project_ote_entry(ll, hh, side)

        atr = _compute_atr(df_h1, period=14)
        if atr <= 0:
            atr = float(df_h1['close'].astype(float).iloc[-1]) * ATR_MIN_PCT

        eq_highs, eq_lows = _liquidity_pools(df_h1)

        if side == "long":
            pool_below = [p for p in eq_lows if p <= ll * (1 + EQ_TOL_PCT)]
            sl_base = max(pool_below) if pool_below else ll
            sl = max(1e-12, sl_base - ATR_SL_MULT * atr)
            tp1_candidate = max(eq_highs) if eq_highs else hh
            tp1 = max(entry + atr, tp1_candidate)
            risk = max(entry - sl, 1e-12)
            tp2 = entry + RR_TARGET_TP2 * risk
        else:
            pool_above = [p for p in eq_highs if p >= hh * (1 - EQ_TOL_PCT)]
            sl_base = min(pool_above) if pool_above else hh
            sl = sl_base + ATR_SL_MULT * atr
            tp1_candidate = min(eq_lows) if eq_lows else ll
            tp1 = min(entry - atr, tp1_candidate)
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
async def handle_symbol_event(ev: Dict[str, Any], rg: "RiskGuard", policy: "MetaPolicy"):
    symbol = ev.get("symbol")
    etype  = ev.get("type")
    if etype != "bar":
        return
    _log_block("EVENT", "bar reçu", symbol=symbol)

    last = _LAST_ANALYSIS_TS.get(symbol, 0.0)
    if time.time() - last < ANALYSIS_MIN_INTERVAL_SEC:
        _log_block("THROTTLE", "skip (analysis cooldown)", symbol=symbol, dt=round(time.time()-last, 3))
        return
    _LAST_ANALYSIS_TS[symbol] = time.time()

    # klines
    try:
        df_h1, df_h4 = _get_klines_cached(symbol)
    except Exception as e:
        log.error("fetch_klines KO: %s", e, extra={"symbol": symbol}, exc_info=True)
        return
    if df_h1 is None and df_h4 is None:
        _log_block("DATA", "klines vides", symbol=symbol)
        return

    # --- Institutionnel + autotune
    inst: Dict[str, Any] = {}
    inst_gate_pass = True
    inst_gate_reason = "n/a"
    comps_cnt = 0
    comps_min = 0
    comps_detail = {}
    thr = {}

    if HAS_INST:
        try:
            inst = get_institutional_snapshot(symbol)
            _log_block(
                "INST",
                "snapshot",
                symbol=symbol,
                score=round(float(inst.get("score", 0) or 0.0), 3),
                oi=round(float(inst.get("oi_score", 0) or 0.0), 3),
                delta=round(float(inst.get("delta_score", 0) or 0.0), 3),
                funding=round(float(inst.get("funding_score", 0) or 0.0), 3),
                liq=round(float(inst.get("liq_score", 0) or 0.0), 3),
                cvd=int(inst.get("delta_cvd_usd", 0) or 0),
                liq5m=int(inst.get("liq_notional_5m", 0) or 0),
            )
        except Exception as e:
            log.warning("inst snapshot KO: %s", e, extra={"symbol": symbol}, exc_info=True)
            inst = {}
    else:
        _log_block("INST", "désactivé (HAS_INST=False)", symbol=symbol)

    if HAS_INST and HAS_TUNER and TUNER is not None:
        try:
            thr = TUNER.update_and_get(symbol, df_h1, inst)  # type: ignore
            comps_cnt, comps_detail = components_ok(inst, thr)  # type: ignore
            comps_min = int(thr.get("components_min", 3))

            score_val = float(inst.get("score", 0) or 0.0)
            req_score = float(thr.get("req_score", 1.2) or 1.2)

            if comps_cnt >= 4:
                inst_gate_pass = True;  inst_gate_reason = "force_pass_4of4"
            elif comps_cnt >= comps_min:
                inst_gate_pass = True;  inst_gate_reason = f"tolerance_pass_{comps_cnt}of{comps_min}"
            elif score_val >= req_score:
                inst_gate_pass = True;  inst_gate_reason = "score_gate"
            else:
                inst_gate_pass = False; inst_gate_reason = "reject"

            _log_block(
                "INST-GATE",
                "résultat",
                symbol=symbol,
                pass_=inst_gate_pass,
                reason=inst_gate_reason,
                score=round(score_val, 3),
                req_score=round(req_score, 3),
                comps=f"{comps_cnt}/{comps_min}",
                q_used=round(float(thr.get("q_used", 0.0)), 4),
                atr_pct=round(float(thr.get("atr_pct", 0.0)), 4),
                comps_detail=comps_detail,
            )

            if not inst_gate_pass:
                try: update_perf_for_symbol(symbol, df_h1=df_h1)
                except Exception: pass
                return
        except Exception as e:
            log.warning("autotune failed: %s", e, extra={"symbol": symbol}, exc_info=True)
            inst_gate_pass = True
            inst_gate_reason = "autotune_fail_bypass"

    # --- Analyse “classique”
    try:
        _log_block("ANALYZE", "lancement", symbol=symbol)
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
        log.error("analyze_signal KO: %s", e, extra={"symbol": symbol}, exc_info=True)
        return

    if not isinstance(res, dict):
        _log_block("ANALYZE", "no-trade (bad result type)", symbol=symbol, type=str(type(res)))
        try: update_perf_for_symbol(symbol, df_h1=df_h1)
        except Exception: pass
        return

    side   = str(res.get("side", "none")).lower()
    rr     = float(res.get("rr", 0) or 0)
    score  = float(res.get("inst_score", inst.get("score", 0) or 0))
    reason = res.get("reason") or ""
    c_list = res.get("comments", []) or []
    comments = ", ".join([str(c) for c in c_list]) if c_list else "—"
    _log_block(
        "ANALYZE",
        "résultat",
        symbol=symbol,
        side=side,
        rr=round(rr, 3),
        inst_score=round(score, 3),
        reason=reason or "—",
        comments=comments,
    )

    diag = (res.get("manage", {}) or {}).get("diagnostics", {})
    tolerated = diag.get("tolerated", res.get("tolerated", []))

    # --- Fallback institutionnel STRUCTURÉ si signal invalide et gate OK
    force_exec = False
    if not res.get("valid", False) or side not in ("long", "short"):
        _log_block(
            "DECISION",
            "signal classique invalide",
            symbol=symbol,
            rr=round(rr, 3),
            inst_score=round(score, 3),
            reason=reason or "—",
            tolerated=tolerated,
            diag=diag,
        )

        if HAS_INST and inst_gate_pass:
            fb = _inst_structured_decision(symbol, inst, df_h1, df_h4)
            if fb:
                res.update(fb)
                side = fb["side"]; rr = float(fb["rr"])
                _log_block(
                    "DECISION",
                    "INST_STRUCT_FALLBACK utilisé",
                    symbol=symbol,
                    side=side,
                    rr=round(rr,3),
                    entry=fmt_price(fb["entry"]),
                    sl=fmt_price(fb["sl"]),
                    tp1=fmt_price(fb["tp1"]),
                    tp2=fmt_price(fb["tp2"]),
                )
                force_exec = True
            else:
                _log_block("DECISION", "fallback institutionnel indisponible", symbol=symbol)
                try: update_perf_for_symbol(symbol, df_h1=df_h1)
                except Exception: pass
                return
        else:
            _log_block("DECISION", "rejete (inst gate fail ou HAS_INST=False)", symbol=symbol)
            try: update_perf_for_symbol(symbol, df_h1=df_h1)
            except Exception: pass
            return

    # --- Risk guard
    rg_ok, rg_reason = rg.can_enter(symbol, ws_latency_ms=50, last_data_age_s=5)
    if not rg_ok:
        _log_block("RISK", "blocked by risk_guard", symbol=symbol, reason=rg_reason)
        return

    # --- Policy (bypass si fallback insti)
    label = "INST_STRUCT" if force_exec else None
    if not force_exec:
        arm, weight, label = policy.choose({"atr_pct": res.get("atr_pct", 0), "adx_proxy": res.get("adx_proxy", 0)})
        _log_block("POLICY", "choix", symbol=symbol, arm=str(arm), weight=round(weight,3), label=str(label))
        if weight < 0.25 and rr < 1.5:
            _log_block("POLICY", "rejete (poids/rr insuffisants)", symbol=symbol, weight=round(weight,3), rr=round(rr,3))
            try: update_perf_for_symbol(symbol, df_h1=df_h1)
            except Exception: pass
            return

    # --- Exécution : SFI d'abord
    entry = float(res.get("entry") or df_h1["close"].astype(float).iloc[-1])
    sl    = float(res.get("sl", 0.0) or 0.0)
    tp1   = float(res.get("tp1", 0.0) or 0.0)
    tp2   = float(res.get("tp2", 0.0) or 0.0)
    value_usdt = float(os.environ.get("ORDER_VALUE_USDT", "20"))

    _log_block(
        "EXEC",
        "préparation",
        symbol=symbol,
        side=side.upper(),
        entry=fmt_price(entry),
        sl=fmt_price(sl),
        tp1=fmt_price(tp1),
        tp2=fmt_price(tp2),
        notional=value_usdt,
        label=label or "META_POLICY",
    )

    try:
        eng = SFIEngine(symbol, side, {"notional": value_usdt, "sl": sl, "tp1": tp1, "tp2": tp2})
    except TypeError:
        eng = SFIEngine(symbol, side, value_usdt, sl, tp1, tp2)

    orders = _safe_place_orders(eng, entry, sl, tp1, tp2)
    orders = _normalize_orders(orders)

    # --- Fallback direct KuCoin si pas d'ID exploitable
    if not _has_real_order_id(orders):
        try:
            meta = get_symbol_meta(symbol) or {}
            tick = float(meta.get("priceIncrement", 0.0)) or 0.0
        except Exception:
            tick = 0.0

        entry_px = _round_to_tick(entry, tick)
        post_only = KC_POST_ONLY_DEFAULT
        side_for_adapter = _side_to_adapter(side)

        _log_block("EXEC-KC", "fallback LIMIT", symbol=symbol, px=fmt_price(entry_px), tick=tick, postOnly=post_only)

        kc = place_limit_order(
            symbol=symbol,
            side=side_for_adapter,
            price=float(entry_px),
            value_usdt=float(value_usdt),
            sl=float(sl),
            tp1=float(tp1),
            tp2=float(tp2),
            post_only=post_only
        )

        clientOid = None
        orderId   = None
        ok_flag   = False
        kc_code   = None

        if isinstance(kc, dict):
            orderId   = kc.get("orderId") or (kc.get("data") or {}).get("orderId")
            clientOid = kc.get("clientOid") or (kc.get("data") or {}).get("clientOid")
            ok_flag   = bool(kc.get("ok", False))
            kc_code   = kc.get("code")
            msg       = kc.get("msg")
            _log_block("EXEC-KC", "place_limit_order retour", symbol=symbol,
                       ok=ok_flag, code=kc_code, msg=msg, clientOid=clientOid, orderId=orderId)

        # Poll clientOid SEULEMENT si POST = succès (code=200000) et pas encore d'orderId
        if (not orderId) and clientOid and ok_flag and (kc_code == "200000") and callable(get_order_by_client_oid or None):  # type: ignore
            for _ in range(KC_VERIFY_MAX_TRIES):
                time.sleep(KC_VERIFY_DELAY_SEC)
                try:
                    od = get_order_by_client_oid(clientOid)  # type: ignore
                except Exception as e:
                    log.debug("verify clientOid error: %s", e, extra={"symbol": symbol})
                    od = None
                if od and isinstance(od, dict):
                    orderId = od.get("orderId") or od.get("id")
                    status  = od.get("status") or od.get("state")
                    _log_block("EXEC-KC", "verify clientOid", symbol=symbol, clientOid=clientOid, status=status, orderId=orderId)
                    if orderId:
                        break

        if orderId:
            orders = [{"ok": True, "orderId": orderId, "clientOid": clientOid, "code": kc_code or "200000"}]
        elif ok_flag and kc_code == "200000" and clientOid:
            orders = [{"ok": True, "orderId": None, "clientOid": clientOid, "code": "200000"}]
        else:
            orders = [{"ok": False, "raw": kc}]
            _log_block("EXEC", "aucun ordre confirmé (voir raw)", symbol=symbol, raw=str(kc)[:240])

    _log_block("EXEC", f"orders={orders}", symbol=symbol)

    # Telegram
    ids = []
    for o in orders or []:
        if isinstance(o, dict):
            oid = o.get("orderId") or o.get("clientOid")
            raw = o.get("raw")
            if not oid and isinstance(raw, str) and raw.strip():
                oid = raw
            if oid: ids.append(str(oid))
    ids_str = ", ".join(ids) if ids else "—"

    lbl = label or "META_POLICY"
    msg = (f"🧠 *{symbol}* — *{side.upper()}* via *{lbl}*\n"
           f"RR: *{res.get('rr','—')}*  |  Entrée: *{fmt_price(entry)}*  |  SL: *{fmt_price(sl)}*  |  TP1: *{fmt_price(tp1)}*  TP2: *{fmt_price(tp2)}*\n"
           f"Score inst.: *{round(float(inst.get('score', 0) or 0.0),3)}*  |  Gate: *{inst_gate_reason}*  |  Composants: *{comps_cnt}/{comps_min}*\n"
           f"Order IDs: {ids_str}")
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
        logging.getLogger("runner").info("[BOOT] symbols n=%d %s", len(symbols), symbols[:20])
    except Exception as e:
        logging.getLogger("runner").error("Build symbols KO: %s", e, exc_info=True)
        symbols = []

    if not symbols:
        logging.getLogger("runner").warning("Aucun symbole.")
        return

    # ✅ DEBUG: vérifie le mode serveur pour chaque symbole, stoppe si Hedge
    try:
        for s in symbols:
            mode = get_server_position_mode(s)
            logging.getLogger("runner").info("[DEBUG] %s positionMode=%s", s, mode)
            if mode == "hedge":
                logging.getLogger("runner").error(
                    "Le serveur est en HEDGE pour %s. Passe le compte/sub-account en One-Way (Single-Side) puis relance.",
                    s
                )
                return
    except Exception as e:
        logging.getLogger("runner").warning("check positionMode impossible: %s", e, exc_info=True)

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
            logging.getLogger("runner").error("handle_symbol_event: %s", e, exc_info=True)

if __name__ == "__main__":
    asyncio.run(main())
