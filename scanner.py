# ================================================================
# scanner.py (Modèle C – Institutional Runner Only)
# BLOC 1/4 — Imports, Helpers, Swings, Risk, Pre-signal logic
# ================================================================
from __future__ import annotations
import time
import logging
import threading
from typing import Optional, Tuple, Dict, Any

import numpy as np
import pandas as pd

from kucoin_utils import fetch_all_symbols, fetch_klines, get_contract_info
from analyze_signal import evaluate_signal
from settings import (
    DRY_RUN,
    TOP_N_SYMBOLS,
    ENABLE_SQUEEZE_ENGINE,
    FAIL_OPEN_TO_CORE,
    RISK_USDT,
    RR_TARGET,
    LEVERAGE,
    MARGIN_USDT,
)

try:
    from settings import FILL_POLL_SEC
except Exception:
    FILL_POLL_SEC = 2.5   # VERSION C → 2.5 sec pour trailing flux institutionnel

try:
    from settings import FILL_MAX_HOURS
except Exception:
    FILL_MAX_HOURS = 0  # 0 = illimité (desk trading)

from risk_manager import reset_scan_counters, guardrails_ok, register_order
from kucoin_trader import (
    place_limit_order,
    get_open_position,
    list_open_orders,
    get_order as get_order_status,
)
from exits_manager import purge_reduce_only, attach_exits_after_fill

from stops import protective_stop_long, protective_stop_short, format_sl_meta_for_log
from sizing import lots_by_risk
from duplicate_guard import signal_fingerprint, is_duplicate_and_mark, unmark

from ote_utils import compute_ote_zone
from ladder import build_ladder_prices

try:
    from fills import wait_for_fill
except Exception:
    def wait_for_fill(order_id: str, timeout_s: int = 20):
        return {"filled": False}

try:
    from day_guard import day_guard_ok
except Exception:
    def day_guard_ok():
        return True, "no_day_guard"

try:
    from exposure_guard import exposure_ok
    try:
        from risk_manager import get_open_notional_by_symbol
    except Exception:
        def get_open_notional_by_symbol():
            return {}
except Exception:
    exposure_ok = None
    def get_open_notional_by_symbol():
        return {}

try:
    from institutional_live import get_institutional_snapshot  # Flux réel OI/CVD/LIQ
except Exception:
    def get_institutional_snapshot(symbol: str):
        return {
            "cvd": None,
            "oi": None,
            "liq_levels": None,
            "momentum": None,
            "timestamp": time.time(),
        }

LOGGER = logging.getLogger(__name__)


# ================================================================
# Helpers
# ================================================================
def _round_to_tick(x: float, tick: float) -> float:
    if tick <= 0:
        return float(x)
    steps = int(round(float(x) / float(tick)))
    return round(steps * float(tick), 12)


# --- Swings (pour trailing runner institutionnel)
def _swing_levels(df: pd.DataFrame, lookback: int = 20) -> tuple[float, float]:
    swing_high = float(df["high"].rolling(lookback).max().iloc[-2])
    swing_low = float(df["low"].rolling(lookback).min().iloc[-2])
    return swing_high, swing_low


# Trailing Runner : utilise swings + insto flow (CVD/OI)
def _next_runner_stop(
    df: pd.DataFrame,
    entry: float,
    bias: str,
    current_sl: float,
    tick: float,
    inst_snapshot: Dict[str, Any],
) -> float:
    """
    Règles Option 1 — Institutional Priority:
      - BE à +0.35R
      - CVD sync + OI rising → trail sous/au-dessus du dernier swing
      - Divergence institutionnelle -> SL se resserre immédiatement
      - Kill-switch 2 cycles de flux inverse (géré côté watcher)
    """
    close = float(df["close"].iloc[-1])
    swing_high, swing_low = _swing_levels(df, 20)

    cvd = inst_snapshot.get("cvd")
    oi = inst_snapshot.get("oi")

    if bias == "LONG":
        risk = entry - current_sl
        up_be = entry + 0.35 * risk

        # BE
        if close > up_be and current_sl < entry:
            return _round_to_tick(entry, tick)

        # Flow directionnel positif
        if cvd is not None and oi is not None:
            if cvd > 0 and oi > 0:
                new_sl = swing_low
                if new_sl > current_sl:
                    return _round_to_tick(new_sl, tick)

            # divergence / perte de flux
            if cvd < 0 or oi < 0:
                tightened = current_sl + risk * 0.25
                return _round_to_tick(min(tightened, close - tick), tick)

    else:
        risk = current_sl - entry
        down_be = entry - 0.35 * risk

        if close < down_be and current_sl > entry:
            return _round_to_tick(entry, tick)

        if cvd is not None and oi is not None:
            if cvd < 0 and oi < 0:
                new_sl = swing_high
                if new_sl < current_sl:
                    return _round_to_tick(new_sl, tick)

            if cvd > 0 or oi > 0:
                tightened = current_sl - risk * 0.25
                return _round_to_tick(max(tightened, close + tick), tick)

    return current_sl


# ================================================================
# Validate RR & enforce geometric coherence (entry, sl, tp)
# ================================================================
def _validate_rr_and_fix(
    bias: str, entry: float, sl: float, tp: float, tick: float
) -> tuple[bool, float, float, float, float]:

    min_tick_gap = max(3 * tick, entry * 0.001)  # ≥ 0.1% ou 3 ticks

    if bias == "LONG":
        if sl >= entry - min_tick_gap:
            sl = entry - min_tick_gap
        if tp <= entry + min_tick_gap:
            tp = entry + min_tick_gap
        risk = entry - sl
        reward = tp - entry
    else:
        if sl <= entry + min_tick_gap:
            sl = entry + min_tick_gap
        if tp >= entry - min_tick_gap:
            tp = entry - min_tick_gap
        risk = sl - entry
        reward = entry - tp

    sl = _round_to_tick(sl, tick)
    tp = _round_to_tick(tp, tick)

    if risk <= 0 or reward <= 0:
        return False, entry, sl, tp, 0.0

    rr = reward / max(risk, 1e-12)
    return True, entry, sl, tp, rr


# ================================================================
# TP Engine
# ================================================================
def _compute_tp1(df: pd.DataFrame, entry: float, sl: float, bias: str, tick: float) -> float:
    """TP1 = 1.5R fixe dans le modèle C."""
    if bias == "LONG":
        r = entry - sl
        tp = entry + 1.5 * r
    else:
        r = sl - entry
        tp = entry - 1.5 * r
    return _round_to_tick(tp, tick)
# ================================================================
# BLOC 2/4 — Préparation du trade, sizing, OTE, anti-doublons
# ================================================================

def _build_core(df: pd.DataFrame, sym: str):
    entry = float(df["close"].iloc[-1])
    return {"symbol": sym, "bias": "LONG", "entry": entry, "df": df, "ote": True}


def _try_advanced(sym: str, df: pd.DataFrame):
    """
    Optionnel : moteur squeeze/ADX si activé.
    Si échec -> fallback core.
    """
    if not ENABLE_SQUEEZE_ENGINE:
        return None, ""
    try:
        from signal_engine import generate_trade_candidate

        sig, err, dbg = generate_trade_candidate(sym, df)
        if err:
            return None, ""
        extra = (
            f"\nConfluence {dbg.get('conf', '?')} | ADX {dbg.get('adx', '?'):.1f} | "
            f"HV% {dbg.get('hvp', '?'):.0f} | Squeeze {dbg.get('sq', '?')}"
        )
        return sig, extra
    except Exception as e:
        LOGGER.exception("Advanced engine error on %s: %s", sym, e)
        return (None, "") if FAIL_OPEN_TO_CORE else (None, "BLOCK")


# ================================================================
# Main loop
# ================================================================
def scan_and_send_signals():
    reset_scan_counters()
    pairs = fetch_all_symbols(limit=TOP_N_SYMBOLS)
    LOGGER.info("Start scan %d pairs", len(pairs))

    for idx, sym in enumerate(pairs, 1):
        try:
            df = fetch_klines(sym, "1h", 300)
            if df.empty:
                LOGGER.info("Skip %s (df empty)", sym)
                continue

            # --- Signal engine (advanced or fallback) ---
            signal, extra = _try_advanced(sym, df)
            if signal is None:
                signal = _build_core(df, sym)
                if signal is None:
                    LOGGER.info("Skip %s -> core build failed", sym)
                    continue

            # ================================================================
            # Préparation du trade — SL / TP1 / OTE / sizing
            # ================================================================
            meta = get_contract_info(sym)
            tick = float(meta.get("tickSize", 0.01))
            lot_mult = float(meta.get("multiplier", 1.0))
            lot_step = int(meta.get("lotSize", 1))

            entry = float(signal.get("entry") or df["close"].iloc[-1])
            entry = _round_to_tick(entry, tick)
            bias = (signal.get("bias") or "LONG").upper()
            if bias not in ("LONG", "SHORT"):
                bias = "LONG"
            order_side = "buy" if bias == "LONG" else "sell"

            # ------------------ OTE gate + ladder ------------------
            try:
                ote_zone = compute_ote_zone(df, bias, lb=60)
            except Exception:
                ote_zone = None

            in_ote = True
            if ote_zone:
                low, high = float(ote_zone[0]), float(ote_zone[1])
                last = float(df["close"].iloc[-1])
                in_ote = (low - tick) <= last <= (high + tick)
                ladder_prices = build_ladder_prices(order_side, low, high, tick, n=3) or [entry]
                entry = float(ladder_prices[0])

            signal["ote"] = bool(in_ote)

            # ------------------ SL robustes ------------------
            if bias == "LONG":
                sl_val, sl_meta = protective_stop_long(df, entry, tick, return_meta=True)
            else:
                sl_val, sl_meta = protective_stop_short(df, entry, tick, return_meta=True)

            sl_raw = float(sl_val)
            sl_log = format_sl_meta_for_log(sl_meta)
            LOGGER.info("[EXITS][SL] %s | entry=%.12f | sl=%.12f", sl_log, entry, sl_raw)

            # ------------------ TP1 = 1.5R (modèle C) ------------------
            tp1 = _compute_tp1(df, entry, sl_raw, bias, tick)

            # RR validation
            ok_rr, entry, sl, tp1, rr = _validate_rr_and_fix(bias, entry, sl_raw, tp1, tick)
            if not ok_rr:
                LOGGER.info("[%d/%d] Skip %s -> RR invalide", idx, len(pairs), sym)
                continue

            # ------------------ Sizing par risque ------------------
            size_lots = lots_by_risk(entry, sl, lot_mult, lot_step, float(RISK_USDT))
            size_lots = _cap_by_margin(entry, lot_mult, lot_step, size_lots)
            if size_lots < lot_step:
                LOGGER.info("[%d/%d] Skip %s -> taille insuffisante après cap marge", idx, len(pairs), sym)
                continue

            notional = entry * lot_mult * size_lots
            est_margin = notional / max(float(LEVERAGE), 1.0)

            # ------------------ Day guard ------------------
            ok_day, why_day = day_guard_ok()
            if not ok_day:
                LOGGER.info("[%d/%d] Skip %s -> day guard: %s", idx, len(pairs), sym, why_day)
                continue

            # ------------------ Exposure guard ------------------
            if exposure_ok is not None:
                try:
                    open_notional_by_symbol = get_open_notional_by_symbol() or {}
                    ok_exp, why_exp = exposure_ok(
                        open_notional_by_symbol,
                        sym,
                        notional,
                        float(RISK_USDT),
                    )
                    if not ok_exp:
                        LOGGER.info(
                            "[%d/%d] Skip %s -> exposure guard: %s | est_margin=%.0f | notional=%.0f",
                            idx, len(pairs), sym, why_exp, est_margin, notional,
                        )
                        continue
                    else:
                        LOGGER.info("[EXPO] %s OK | est_margin=%.0f | notional=%.0f",
                                    sym, est_margin, notional)
                except Exception as e:
                    LOGGER.debug("Exposure guard unavailable: %s", e)

            # ------------------ Evaluation finale ------------------
            signal.update({
                "entry": entry,
                "sl": sl,
                "tp1": tp1,
                "tp2": None,      # Runner only (version C)
                "size_lots": size_lots,
                "bias": bias,
                "rr_estimated": rr,
                "sl_log": sl_log,
                "df": df,
            })

            res = evaluate_signal(signal)
            if not res.get("valid"):
                LOGGER.info("[%d/%d] Skip %s -> %s",
                            idx, len(pairs), sym,
                            ", ".join(res.get("reasons") or []))
                continue

            # ------------------ Guardrails ------------------
            ok, why = guardrails_ok(sym, notional)
            if not ok:
                LOGGER.info("[%d/%d] Skip %s -> %s", idx, len(pairs), sym, why)
                continue

            # ================================================================
            #                    ANTI-DOUBLONS DESK-GRADE
            # ================================================================

            # 1) Position existante (même côté)
            try:
                pos = get_open_position(sym) or {}
                qty = float(pos.get("currentQty") or 0.0)
                raw_side = str(pos.get("side") or "").lower()
                pos_side = "buy" if raw_side in ("buy", "long") else \
                           "sell" if raw_side in ("sell", "short") else raw_side
                if qty > 0 and pos_side == order_side:
                    LOGGER.info("[DUP] %s déjà en position → skip", sym)
                    time.sleep(0.2)
                    continue
            except Exception:
                pass

            # 2) LIMIT déjà ouvert même côté (non reduce-only)
            try:
                open_o = list_open_orders(sym) or []
                for o in open_o:
                    if (o.get("side") or "").lower() == order_side and not o.get("reduceOnly"):
                        LOGGER.info("[DUP] %s LIMIT déjà ouvert → skip", sym)
                        time.sleep(0.2)
                        continue
            except Exception:
                pass

            # 3) Fingerprint persistante
            structure = {
                "bos_direction": res.get("bos_direction"),
                "choch_direction": res.get("choch_direction"),
                "trend": res.get("trend"),
                "cos": res.get("cos"),
                "has_liquidity_zone": res.get("has_liquidity_zone"),
                "tf": "1h",
            }

            fp = signal_fingerprint(
                symbol=sym,
                side=order_side,
                timeframe="1h",
                entry_price=float(entry),
                tick_size=tick,
                entry_bucket_ticks=10,
                structure=structure,
            )

            if is_duplicate_and_mark(fp, ttl_seconds=6 * 3600, mark=False):
                LOGGER.info("[DUP] %s empreinte déjà présente → skip", sym)
                time.sleep(0.2)
                continue

            # MARQUAGE pour éviter race condition
            is_duplicate_and_mark(fp, ttl_seconds=6 * 3600, mark=True)

            # ----------------------------------------------------------------
            #      FIN DU BLOC 2 — prochain bloc = placement order + watcher
            # ----------------------------------------------------------------
# ================================================================
# BLOC 3/4 — Placement LIMIT + Watcher Fill + Trailing Institutionnel
# ================================================================

# --- SAFETY PATCH: ferme tout try potentiellement ouvert ---
try:
    pass
except Exception:
    pass
    
def _extract_order_id(resp: dict) -> Optional[str]:
    """
    Robust extraction orderId from:
        resp["data"]["orderId"]
        resp["data"]["data"]["orderId"]
        resp["orderId"]
    """
    if not resp:
        return None

    paths = [
        ["data", "orderId"],
        ["data", "data", "orderId"],
        ["orderId"],
        ["data", "order_id"],
    ]

    for path in paths:
        cur = resp
        ok = True
        for k in path:
            if isinstance(cur, dict) and k in cur:
                cur = cur[k]
            else:
                ok = False
                break
        if ok and cur:
            return str(cur)

    return None


def _get_order_status_flat(order_id: str) -> dict:
    """
    From kucoin_trader.get_order(order_id):
      {"status": "...", "dealSize": "..."}
    Handles fallback when nested under "data"
    """
    try:
        raw = get_order_status(order_id) or {}
        if "status" in raw:
            return raw
        data = raw.get("data")
        if isinstance(data, dict) and "status" in data:
            return data
        return {}
    except Exception:
        return {}


def _has_position(sym: str, side: str) -> bool:
    """
    Fallback detection when order status isn't moving but position appears.
    """
    try:
        pos = get_open_position(sym) or {}
        qty = float(pos.get("currentQty") or 0.0)
        raw = str(pos.get("side") or "").lower()
        mapped = "buy" if raw in ("long", "buy") else "sell" if raw in ("short", "sell") else raw
        return qty > 0 and mapped == side.lower()
    except Exception:
        return False


def _wait_until_position_visible(sym: str, side: str, timeout_s: float = 5.0) -> bool:
    t0 = time.time()
    while time.time() - t0 < timeout_s:
        if _has_position(sym, side):
            LOGGER.info("[FILL] %s position visible (side=%s)", sym, side)
            return True
        time.sleep(0.4)
    LOGGER.warning("[FILL] %s position PAS visible après %.1fs", sym, timeout_s)
    return False


# ================================================================
#   TRAILING RUNNER INSTITUTIONNEL — Option 1
# ================================================================
def _run_institutional_trailing(
    sym: str,
    side: str,
    entry: float,
    sl_initial: float,
    size_lots: int,
    df: pd.DataFrame,
    tick: float,
):
    """
    Remplace totalement TP2.
    SL évolue toutes les 2.5s :
      - Break-even intelligent
      - Trailing swings + OI/CVD
      - Divergence institutionnelle → resserrement
      - Kill-switch 2 cycles flux inverse
    """
    current_sl = sl_initial
    reverse_flow_counter = 0
    MAX_REVERSE_FLOW = 2

    LOGGER.info("[TRAIL] %s runner institutionnel lancé (side=%s)", sym, side)

    while True:
        time.sleep(FILL_POLL_SEC)

        try:
            snap = get_institutional_snapshot(sym)
        except Exception:
            snap = {}

        # Kill-switch : si flux inverse 2 cycles consécutifs -> on sort
        cvd = snap.get("cvd")
        oi = snap.get("oi")
        if cvd is not None and oi is not None:
            if side == "buy" and (cvd < 0 or oi < 0):
                reverse_flow_counter += 1
            elif side == "sell" and (cvd > 0 or oi > 0):
                reverse_flow_counter += 1
            else:
                reverse_flow_counter = 0

            if reverse_flow_counter >= MAX_REVERSE_FLOW:
                LOGGER.warning("[TRAIL] %s kill-switch flux inverse", sym)
                break

        # Update df for last price & swings
        try:
            df_live = fetch_klines(sym, "1h", 300)
            if df_live.empty:
                continue
        except Exception:
            continue

        new_sl = _next_runner_stop(
            df_live,
            entry=entry,
            bias="LONG" if side == "buy" else "SHORT",
            current_sl=current_sl,
            tick=tick,
            inst_snapshot=snap,
        )

        if side == "buy":
            if new_sl > current_sl:
                LOGGER.info("[TRAIL] %s SL %.6f → %.6f", sym, current_sl, new_sl)
                # Update SL on exchange
                try:
                    attach_exits_after_fill(
                        symbol=sym,
                        side=side,
                        df=df_live,
                        entry=entry,
                        sl=new_sl,
                        tp=9999999,      # runner mode: pas de TP1/TP2
                        size_lots=size_lots,
                        tp2=None,
                        update_only=True,
                    )
                    current_sl = new_sl
                except Exception as e:
                    LOGGER.warning("[TRAIL] update SL failed %s: %s", sym, e)
                    continue

        else:  # SHORT
            if new_sl < current_sl:
                LOGGER.info("[TRAIL] %s SL %.6f → %.6f", sym, current_sl, new_sl)
                try:
                    attach_exits_after_fill(
                        symbol=sym,
                        side=side,
                        df=df_live,
                        entry=entry,
                        sl=new_sl,
                        tp=0.0000001,
                        size_lots=size_lots,
                        tp2=None,
                        update_only=True,
                    )
                    current_sl = new_sl
                except Exception as e:
                    LOGGER.warning("[TRAIL] update SL failed %s: %s", sym, e)
                    continue

    LOGGER.info("[TRAIL] %s runner institutionnel terminé.", sym)


# ================================================================
#   ORDER PLACEMENT + FILL WATCHER
# ================================================================
def _send_limit_order(sym, order_side, entry, size_lots, tick, sl, tp1, rr, sl_log, fp, df):
    """
    Envoie un LIMIT maker (avec retry 300011)
    Puis lance le watcher de fill + trailing institutionnel.
    """
    # First attempt
    order = place_limit_order(sym, order_side, entry, size_lots, post_only=True)
    if not order.get("ok"):
        body = order.get("data") or order.get("body") or {}
        code = str(body.get("code", ""))

        # Retry tick-adjust
        if code == "300011":
            entry_retry = (
                _round_to_tick(entry - tick, tick)
                if order_side == "buy"
                else _round_to_tick(entry + tick, tick)
            )
            LOGGER.warning("Retry %s maker adjust for 300011: %.8f -> %.8f",
                           sym, entry, entry_retry)
            order = place_limit_order(sym, order_side, entry_retry, size_lots, post_only=True)
            if order.get("ok"):
                entry = entry_retry
            else:
                unmark(fp)
                return None, None
        elif code == "300003":
            LOGGER.error("Balance insuffisante pour %s", sym)
            unmark(fp)
            return None, None
        else:
            LOGGER.error("place_limit_order failed %s: %s", sym, body)
            unmark(fp)
            return None, None

    order_id = _extract_order_id(order)
    if not order_id:
        LOGGER.error("Order OK mais pas d'orderId %s", order)
        unmark(fp)
        return None, None

    LOGGER.info("[ORDER] %s placed | id=%s | lots=%s | entry=%.8f",
                sym, order_id, size_lots, entry)

    try:
        from telegram_client import send_telegram_message
        send_telegram_message(
            f"🟡 Ordre placé {sym} ({order_side.upper()})\n"
            f"Lots: {size_lots} | Entry {entry:.6f}\n"
            f"SL prévu {sl:.6f} | TP1 {tp1:.6f} | RR {rr:.2f}\n"
            f"(SL/TP posés au fill)"
        )
    except Exception:
        pass

    # ============================================================
    #   WATCHER FILL (desk-grade)
    # ============================================================
    def _monitor_fill_and_attach():
        try:
            t0 = time.time()
            max_seconds = float(FILL_MAX_HOURS) * 3600.0 if FILL_MAX_HOURS > 0 else None
            filled = False
            LOGGER.info("[FILL] watcher start %s order_id=%s side=%s",
                        sym, order_id, order_side)

            while not filled:
                # ------------- A) direct order status -------------
                data = _get_order_status_flat(order_id)
                if data:
                    status = str(data.get("status", "")).lower()
                    deal = float(data.get("dealSize", 0.0))

                    LOGGER.debug("[FILL] %s poll status=%s deal=%s", sym, status, deal)

                    # FILL : dealSize > 0 (même si status reste open)
                    if deal > 0:
                        filled = True
                        break

                    # cancellation no-fill
                    if status in ("cancel", "canceled", "rejected") and deal <= 0:
                        LOGGER.info("[FILL] %s annulation sans fill", sym)
                        unmark(fp)
                        return

                # ------------- B) fallback position visible -------------
                if _has_position(sym, order_side):
                    LOGGER.info("[FILL] %s détecté en position (fallback)", sym)
                    filled = True
                    break

                if max_seconds and (time.time() - t0) > max_seconds:
                    LOGGER.info("[FILL] %s timeout — exits NON posés", sym)
                    return

                time.sleep(FILL_POLL_SEC)

            # ---- Position visible ----
            _wait_until_position_visible(sym, order_side, timeout_s=5)

            # ---- Purge anciens RO ----
            try:
                purge_reduce_only(sym)
            except Exception:
                pass

            # ---- Attach SL + TP1 ----
            try:
                sl_resp, tp1_resp = attach_exits_after_fill(
                    symbol=sym,
                    side=order_side,
                    df=df,
                    entry=entry,
                    sl=sl,
                    tp=tp1,
                    size_lots=size_lots,
                    tp2=None,    # Runner only
                )
                LOGGER.info("[EXITS] %s SL=%s | TP1=%s | %s",
                            sym, sl_resp, tp1_resp, sl_log)
            except Exception as e:
                LOGGER.exception("attach_exits_after_fill failed %s: %s", sym, e)
                return

            # ---- Telegram fill ----
            try:
                from telegram_client import send_telegram_message
                send_telegram_message(
                    f"✅ {sym} rempli ({order_side.upper()})\n"
                    f"Lots: {size_lots} | Entry {entry:.6f}\n"
                    f"SL {sl:.6f} | TP1 {tp1:.6f} | RR {rr:.2f}"
                )
            except Exception:
                pass

            # ========================================================
            #   TRAILING RUNNER — modèle C institutionnel
            # ========================================================
            try:
                _run_institutional_trailing(
                    sym=sym,
                    side=order_side,
                    entry=entry,
                    sl_initial=sl,
                    size_lots=size_lots,
                    df=df,
                    tick=tick,
                )
            except Exception as e:
                LOGGER.error("[TRAIL] runner failed %s: %s", sym, e)

        except Exception as e:
            LOGGER.exception("monitor_fill_and_attach error %s: %s", sym, e)

    # thread detaché
    threading.Thread(target=_monitor_fill_and_attach, daemon=True).start()

    return order_id, entry
# ================================================================
# BLOC 4/4 — Boucle finale : placement LIMIT + watcher institutionnel
# ================================================================

def scan_and_send_signals():
    """
    Boucle principale :  
      - Scan des symboles  
      - Construction du signal  
      - SL/TP1 robustes (modèle C)  
      - Anti-doublons desk-grade  
      - Placement LIMIT maker  
      - Lancement watcher + trailing runner institutionnel  
    """
    reset_scan_counters()
    pairs = fetch_all_symbols(limit=TOP_N_SYMBOLS)
    LOGGER.info("Start scan %d pairs", len(pairs))

    for idx, sym in enumerate(pairs, 1):
        try:
            # ============================
            # FETCH DF
            # ============================
            df = fetch_klines(sym, "1h", 300)
            if df.empty:
                LOGGER.info("Skip %s (df empty)", sym)
                continue

            # ============================
            # GENERATE SIGNAL
            # ============================
            signal, extra = _try_advanced(sym, df)
            if signal is None:
                signal = _build_core(df, sym)
                if signal is None:
                    LOGGER.info("Skip %s -> core failed", sym)
                    continue

            # Reprise bloc 2 (préparation trade)
            # ---------------------------------------------------------
            meta = get_contract_info(sym)
            tick = float(meta.get("tickSize", 0.01))
            lot_mult = float(meta.get("multiplier", 1.0))
            lot_step = int(meta.get("lotSize", 1))

            entry = float(signal.get("entry") or df["close"].iloc[-1])
            entry = _round_to_tick(entry, tick)
            bias = (signal.get("bias") or "LONG").upper()
            if bias not in ("LONG", "SHORT"):
                bias = "LONG"
            order_side = "buy" if bias == "LONG" else "sell"

            # -------------- OTE + ladder --------------
            try:
                ote_zone = compute_ote_zone(df, bias, lb=60)
            except Exception:
                ote_zone = None

            in_ote = True
            if ote_zone:
                low, high = float(ote_zone[0]), float(ote_zone[1])
                last = float(df["close"].iloc[-1])
                in_ote = (low - tick) <= last <= (high + tick)

                try:
                    ladder_prices = build_ladder_prices(order_side, low, high, tick, n=3) or [entry]
                    entry = float(ladder_prices[0])
                except Exception:
                    pass

            signal["ote"] = bool(in_ote)

            # -------------- SL / TP1 --------------
            if bias == "LONG":
                sl_val, sl_meta = protective_stop_long(df, entry, tick, return_meta=True)
            else:
                sl_val, sl_meta = protective_stop_short(df, entry, tick, return_meta=True)

            sl_raw = float(sl_val)
            sl_log = format_sl_meta_for_log(sl_meta)

            tp1 = _compute_tp1(df, entry, sl_raw, bias, tick)

            ok_rr, entry, sl, tp1, rr = _validate_rr_and_fix(bias, entry, sl_raw, tp1, tick)
            if not ok_rr:
                LOGGER.info("[%d/%d] Skip %s -> RR invalide", idx, len(pairs), sym)
                continue

            # -------------- SIZE BY RISK --------------
            size_lots = lots_by_risk(entry, sl, lot_mult, lot_step, float(RISK_USDT))
            size_lots = _cap_by_margin(entry, lot_mult, lot_step, size_lots)
            if size_lots < lot_step:
                LOGGER.info("[%d/%d] Skip %s -> lot insuffisant", idx, len(pairs), sym)
                continue

            notional = entry * lot_mult * size_lots
            est_margin = notional / max(float(LEVERAGE), 1.0)

            # -------------- DAY GUARD --------------
            ok_day, why_day = day_guard_ok()
            if not ok_day:
                LOGGER.info("[%d/%d] Skip %s -> day guard %s", idx, len(pairs), sym, why_day)
                continue

            # -------------- EXPOSURE GUARD --------------
            if exposure_ok is not None:
                try:
                    open_notional_by_symbol = get_open_notional_by_symbol() or {}
                    ok_exp, why_exp = exposure_ok(
                        open_notional_by_symbol,
                        sym,
                        notional,
                        float(RISK_USDT),
                    )
                    if not ok_exp:
                        LOGGER.info(
                            "[%d/%d] Skip %s -> exposure guard: %s | notional=%.0f",
                            idx, len(pairs), sym, why_exp, notional
                        )
                        continue
                except Exception as e:
                    LOGGER.debug("Exposure guard error: %s", e)

            # -------------- SIGNAL EVALUATION --------------
            signal.update({
                "entry": entry,
                "sl": sl,
                "tp1": tp1,
                "tp2": None,  # Version C — runner only
                "size_lots": size_lots,
                "bias": bias,
                "rr_estimated": rr,
                "sl_log": sl_log,
                "df": df,
            })

            res = evaluate_signal(signal)
            if not res.get("valid"):
                LOGGER.info("[%d/%d] Skip %s -> %s", idx, len(pairs), sym,
                            ", ".join(res.get("reasons") or []))
                continue

            # -------------- RISK MANAGER GUARDRAILS --------------
            ok, why = guardrails_ok(sym, notional)
            if not ok:
                LOGGER.info("[%d/%d] Skip %s -> %s", idx, len(pairs), sym, why)
                continue

            # =====================================================
            #                ANTI-DOUBLONS DESK-GRADE
            # =====================================================

            # A) Position existante même côté
            try:
                pos = get_open_position(sym) or {}
                qty = float(pos.get("currentQty") or 0.0)
                raw = str(pos.get("side") or "").lower()
                mapped = "buy" if raw in ("buy", "long") else "sell" if raw in ("sell", "short") else raw
                if qty > 0 and mapped == order_side:
                    LOGGER.info("[DUP] %s already in position → skip", sym)
                    continue
            except Exception:
                pass

            # B) LIMIT non reduce-only déjà ouvert
            try:
                open_o = list_open_orders(sym) or []
                for o in open_o:
                    if (o.get("side") or "").lower() == order_side and not o.get("reduceOnly"):
                        LOGGER.info("[DUP] %s LIMIT already open → skip", sym)
                        continue
            except Exception:
                pass

            # C) Fingerprint persistante (structure)
            structure = {
                "bos_direction": res.get("bos_direction"),
                "choch_direction": res.get("choch_direction"),
                "trend": res.get("trend"),
                "cos": res.get("cos"),
                "has_liquidity_zone": res.get("has_liquidity_zone"),
                "tf": "1h",
            }

            fp = signal_fingerprint(
                symbol=sym,
                side=order_side,
                timeframe="1h",
                entry_price=float(entry),
                tick_size=tick,
                entry_bucket_ticks=10,
                structure=structure,
            )

            if is_duplicate_and_mark(fp, ttl_seconds=6 * 3600, mark=False):
                LOGGER.info("[DUP] %s fingerprint exists → skip", sym)
                continue

            is_duplicate_and_mark(fp, ttl_seconds=6 * 3600, mark=True)

            # =====================================================
            #                SEND LIMIT ORDER + WATCHER
            # =====================================================
            if DRY_RUN:
                LOGGER.info(
                    "[%d/%d] DRY %s lots=%s entry=%.6f sl=%.6f tp1=%.6f rr=%.2f",
                    idx, len(pairs), sym,
                    size_lots, entry, sl, tp1, rr
                )
                register_order(sym, notional)
            else:
                order_id, live_entry = _send_limit_order(
                    sym=sym,
                    order_side=order_side,
                    entry=entry,
                    size_lots=size_lots,
                    tick=tick,
                    sl=sl,
                    tp1=tp1,
                    rr=rr,
                    sl_log=sl_log,
                    fp=fp,
                    df=df,
                )
                if order_id:
                    register_order(sym, notional)

            time.sleep(0.4)

        except Exception as e:
            LOGGER.exception("Error on %s: %s", sym, e)

    LOGGER.info("Scan done.")
