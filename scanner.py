# ================================================================
# scanner.py — Version C PRO (Institutional Runner Only)
# BLOC 1/4 — Imports, Helpers, RR, TP, Swings
# ================================================================
from __future__ import annotations
import time
import logging
import threading
from typing import Optional, Dict, Any, Tuple

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

# watcher cadence
try:
    from settings import FILL_POLL_SEC
except Exception:
    FILL_POLL_SEC = 2.5  # runner institutionnel → cadence haute

try:
    from settings import FILL_MAX_HOURS
except Exception:
    FILL_MAX_HOURS = 0   # 0 = illimité

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

# fallback fills module
try:
    from fills import wait_for_fill
except Exception:
    def wait_for_fill(order_id: str, timeout_s: int = 20):
        return {"filled": False}

# day guard
try:
    from day_guard import day_guard_ok
except Exception:
    def day_guard_ok():
        return True, "no_day_guard"

# exposure guard
try:
    from exposure_guard import exposure_ok
    from risk_manager import get_open_notional_by_symbol
except Exception:
    exposure_ok = None
    def get_open_notional_by_symbol():
        return {}

# live institutional (OI / CVD / liquidations)
try:
    from institutional_live import get_institutional_snapshot
except Exception:
    def get_institutional_snapshot(symbol: str):
        return {
            "cvd": None,
            "oi": None,
            "momentum": None,
            "liq_levels": None,
            "timestamp": time.time(),
        }

LOGGER = logging.getLogger(__name__)


# ================================================================
# Helpers
# ================================================================

def _round_to_tick(x: float, tick: float) -> float:
    """Arrondit toujours sur un multiple exact du tick."""
    if tick <= 0:
        return float(x)
    steps = int(round(float(x) / float(tick)))
    return round(steps * float(tick), 12)


def _swing_levels(df: pd.DataFrame, lookback: int = 20) -> Tuple[float, float]:
    """Retourne swing-high & swing-low propres pour trailing."""
    swing_high = float(df["high"].rolling(lookback).max().iloc[-2])
    swing_low = float(df["low"].rolling(lookback).min().iloc[-2])
    return swing_high, swing_low


# ================================================================
# Institutional Runner — Prochaine position du SL
# ================================================================
def _next_runner_stop(
    df: pd.DataFrame,
    entry: float,
    bias: str,
    current_sl: float,
    tick: float,
    inst_snapshot: Dict[str, Any]
) -> float:
    """
    Règles runner institutionnel :
      - BE intelligent = +0.35R
      - CVD + OI synchrones ⇒ SL sur swing
      - Divergence institutionnelle ⇒ SL se resserre
    """
    close = float(df["close"].iloc[-1])
    swing_high, swing_low = _swing_levels(df, 20)

    cvd = inst_snapshot.get("cvd")
    oi = inst_snapshot.get("oi")

    if bias == "LONG":
        risk = entry - current_sl
        be = entry + 0.35 * risk

        # Break-even
        if close > be and current_sl < entry:
            return _round_to_tick(entry, tick)

        if cvd is not None and oi is not None:
            # flux positif → trailing sur swing
            if cvd > 0 and oi > 0:
                new_sl = swing_low
                if new_sl > current_sl:
                    return _round_to_tick(new_sl, tick)

            # divergence → resserrement immédiat
            if cvd < 0 or oi < 0:
                tightened = current_sl + risk * 0.25
                return _round_to_tick(min(tightened, close - tick), tick)

    else:  # SHORT
        risk = current_sl - entry
        be = entry - 0.35 * risk

        if close < be and current_sl > entry:
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
# RR coherence (entry / SL / TP1)
# ================================================================
def _validate_rr_and_fix(
    bias: str, entry: float, sl: float, tp: float, tick: float
) -> Tuple[bool, float, float, float, float]:

    min_tick_gap = max(3 * tick, entry * 0.001)  # >= 0.1 % ou 3 ticks

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
# TP1 = 1.5R (Modèle C)
# ================================================================
def _compute_tp1(df: pd.DataFrame, entry: float, sl: float, bias: str, tick: float) -> float:
    if bias == "LONG":
        r = entry - sl
        tp = entry + 1.5 * r
    else:
        r = sl - entry
        tp = entry - 1.5 * r
    return _round_to_tick(tp, tick)

# ================================================================
# BLOC 2/4 — Préparation du trade : signal, OTE, SL/TP1, sizing, anti-doublons
# ================================================================

def _build_core(df: pd.DataFrame, sym: str):
    """Fallback simple si le moteur avancé échoue."""
    entry = float(df["close"].iloc[-1])
    return {"symbol": sym, "bias": "LONG", "entry": entry, "df": df, "ote": True}


def _try_advanced(sym: str, df: pd.DataFrame):
    """Optionnel : moteur ADX/Squeeze si activé."""
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
        LOGGER.exception("Advanced engine error %s: %s", sym, e)
        return (None, "") if FAIL_OPEN_TO_CORE else (None, "BLOCK")


# ----------------------------------------------------------------
#           CAP MARGIN (léverage & marge max)
# ----------------------------------------------------------------
def _cap_by_margin(entry: float, lot_mult: float, lot_step: int, size_lots: int) -> int:
    """Réduit la taille si la marge dépasse MARGIN_USDT."""
    if LEVERAGE <= 0:
        return size_lots

    max_lots = int((MARGIN_USDT * LEVERAGE) // max(entry * lot_mult, 1e-12))
    max_lots -= (max_lots % max(1, lot_step))

    return max(lot_step, min(size_lots, max_lots))


# ================================================================
# Main Loop — scan analyse (Bloc 2)
# ================================================================
def _prepare_trade(sym: str, df: pd.DataFrame, idx: int, total: int):
    """
    Extrait et prépare :
      - signal
      - entry/SL/TP1 propre
      - sizing
      - checks risk manager
      - anti-doublons
    Retourne : (signal, res, metadata) OU None si skip
    """

    # ---------------------------------------------
    # Génération signal (advanced engine → fallback)
    # ---------------------------------------------
    signal, extra = _try_advanced(sym, df)
    if signal is None:
        signal = _build_core(df, sym)
        if signal is None:
            LOGGER.info("Skip %s -> core failed", sym)
            return None

    # ---------------------------------------------
    # Récupération metadata contrat
    # ---------------------------------------------
    meta = get_contract_info(sym)
    tick = float(meta.get("tickSize", 0.01))
    lot_mult = float(meta.get("multiplier", 1.0))
    lot_step = int(meta.get("lotSize", 1))

    # ---------------------------------------------
    # Entry
    # ---------------------------------------------
    entry = float(signal.get("entry") or df["close"].iloc[-1])
    entry = _round_to_tick(entry, tick)

    bias = (signal.get("bias") or "LONG").upper()
    if bias not in ("LONG", "SHORT"):
        bias = "LONG"

    order_side = "buy" if bias == "LONG" else "sell"

    # ============================================================
    # OTE Gate + Ladder Maker
    # ============================================================
    try:
        ote_zone = compute_ote_zone(df, bias, lb=60)
    except Exception:
        ote_zone = None

    in_ote = True
    if ote_zone:
        low, high = float(ote_zone[0]), float(ote_zone[1])
        last = float(df["close"].iloc[-1])

        in_ote = (low - tick) <= last <= (high + tick)

        # Ladder 3 niveaux si activé
        try:
            ladder_prices = build_ladder_prices(order_side, low, high, tick, n=3) or [entry]
            entry = float(ladder_prices[0])
        except Exception:
            pass

    signal["ote"] = in_ote

    # ============================================================
    # SL (Protective institutional) + TP1 (1.5R model C)
    # ============================================================
    if bias == "LONG":
        sl_val, sl_meta = protective_stop_long(df, entry, tick, return_meta=True)
    else:
        sl_val, sl_meta = protective_stop_short(df, entry, tick, return_meta=True)

    sl_raw = float(sl_val)
    sl_log = format_sl_meta_for_log(sl_meta)

    tp1 = _compute_tp1(df, entry, sl_raw, bias, tick)

    ok_rr, entry, sl, tp1, rr = _validate_rr_and_fix(bias, entry, sl_raw, tp1, tick)
    if not ok_rr:
        LOGGER.info("[%d/%d] Skip %s -> RR invalide", idx, total, sym)
        return None

    # ============================================================
    # Sizing par risque + cap par marge
    # ============================================================
    size_lots = lots_by_risk(entry, sl, lot_mult, lot_step, float(RISK_USDT))
    size_lots = _cap_by_margin(entry, lot_mult, lot_step, size_lots)

    if size_lots < lot_step:
        LOGGER.info("[%d/%d] Skip %s -> taille insuffisante", idx, total, sym)
        return None

    notional = entry * lot_mult * size_lots
    est_margin = notional / max(float(LEVERAGE), 1.0)

    # ============================================================
    # Day guard
    # ============================================================
    ok_day, why_day = day_guard_ok()
    if not ok_day:
        LOGGER.info("[%d/%d] Skip %s -> day guard %s", idx, total, sym, why_day)
        return None

    # ============================================================
    # Exposure guard
    # ============================================================
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
                    idx, total, sym, why_exp, notional
                )
                return None
        except Exception as e:
            LOGGER.debug("Exposure guard error: %s", e)

    # ============================================================
    # Final Signal Evaluation
    # ============================================================
    signal.update({
        "entry": entry,
        "sl": sl,
        "tp1": tp1,
        "tp2": None,
        "size_lots": size_lots,
        "bias": bias,
        "rr_estimated": rr,
        "sl_log": sl_log,
        "df": df,
    })

    res = evaluate_signal(signal)
    if not res.get("valid"):
        LOGGER.info("[%d/%d] Skip %s -> %s",
                    idx, total, sym,
                    ", ".join(res.get("reasons") or []))
        return None

    # ============================================================
    # Anti-doublons desk grade
    # ============================================================

    # A — déjà en position
    try:
        pos = get_open_position(sym) or {}
        qty = float(pos.get("currentQty") or 0.0)
        raw = str(pos.get("side") or "").lower()
        mapped = "buy" if raw in ("buy", "long") else "sell" if raw in ("sell", "short") else raw

        if qty > 0 and mapped == order_side:
            LOGGER.info("[DUP] %s already in position → skip", sym)
            return None
    except Exception:
        pass

    # B — LIMIT non reduce-only déjà ouvert
    try:
        open_o = list_open_orders(sym) or []
        for o in open_o:
            if (o.get("side") or "").lower() == order_side and not o.get("reduceOnly"):
                LOGGER.info("[DUP] %s LIMIT déjà ouvert → skip", sym)
                return None
    except Exception:
        pass

    # C — fingerprint structurelle
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
        return None

    # marquage effectif
    is_duplicate_and_mark(fp, ttl_seconds=6 * 3600, mark=True)

    # -----------------------------------------------------------
    # RETURN success
    # -----------------------------------------------------------
    return {
        "signal": signal,
        "res": res,
        "entry": entry,
        "sl": sl,
        "tp1": tp1,
        "rr": rr,
        "size_lots": size_lots,
        "order_side": order_side,
        "tick": tick,
        "lot_mult": lot_mult,
        "lot_step": lot_step,
        "fp": fp,
        "df": df,
    }

# ================================================================
# BLOC 3/4 — Placement LIMIT + Watcher Fill + Trailing Institutionnel
# ================================================================

def _extract_order_id(resp: dict) -> Optional[str]:
    """Extraction robuste du orderId kucoin."""
    if not resp:
        return None

    paths = [
        ["data", "orderId"],
        ["data", "data", "orderId"],
        ["orderId"],
        ["data", "order_id"],
    ]
    for p in paths:
        cur = resp
        ok = True
        for k in p:
            if isinstance(cur, dict) and k in cur:
                cur = cur[k]
            else:
                ok = False
                break
        if ok and cur:
            return str(cur)
    return None


def _get_order_status_flat(order_id: str) -> dict:
    """Normalise réponse get_order pour obtenir status, dealSize."""
    try:
        raw = get_order_status(order_id) or {}
        if "status" in raw:
            return raw
        data = raw.get("data")
        if isinstance(data, dict) and "status" in data:
            return data
    except Exception:
        pass
    return {}


def _has_position(sym: str, side: str) -> bool:
    """Fallback si l’ordre ne bouge pas mais que la position apparaît."""
    try:
        pos = get_open_position(sym) or {}
        qty = float(pos.get("currentQty") or 0.0)
        raw = str(pos.get("side") or "").lower()
        mapped = "buy" if raw in ("long", "buy") else "sell" if raw in ("short", "sell") else raw
        return qty > 0 and mapped == side.lower()
    except Exception:
        return False


def _wait_until_position_visible(sym: str, side: str, timeout_s: float = 5.0) -> bool:
    """Attends que KuCoin expose la position pour éviter 300009."""
    t0 = time.time()
    while time.time() - t0 < timeout_s:
        if _has_position(sym, side):
            LOGGER.info("[FILL] %s position visible (side=%s)", sym, side)
            return True
        time.sleep(0.4)
    LOGGER.warning("[FILL] %s position PAS visible après %.1fs", sym, timeout_s)
    return False


# ================================================================
# TRAILING RUNNER INSTITUTIONNEL — Option 1 (modèle C)
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
    Runner institutionnel :
      - Break-even intelligent à 0.35R
      - Trailing par swings + direction OI/CVD
      - Divergence insta → resserrer
      - Kill-switch flux inverse 2 cycles
    """
    current_sl = sl_initial
    reverse_flow_counter = 0
    MAX_REV = 2

    LOGGER.info("[TRAIL] %s RUNNER institutions lancé (side=%s)", sym, side)

    while True:
        time.sleep(FILL_POLL_SEC)

        # Snapshot institutionnel
        try:
            snap = get_institutional_snapshot(sym)
        except Exception:
            snap = {}

        cvd = snap.get("cvd")
        oi = snap.get("oi")

        # Kill-switch
        if cvd is not None and oi is not None:
            if side == "buy" and (cvd < 0 or oi < 0):
                reverse_flow_counter += 1
            elif side == "sell" and (cvd > 0 or oi > 0):
                reverse_flow_counter += 1
            else:
                reverse_flow_counter = 0

            if reverse_flow_counter >= MAX_REV:
                LOGGER.warning("[TRAIL] %s kill-switch flux inverse", sym)
                break

        # Update df pour swings
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

        # Mise à jour SL si amélioré
        if side == "buy" and new_sl > current_sl:
            LOGGER.info("[TRAIL] %s SL %.6f → %.6f", sym, current_sl, new_sl)
            try:
                attach_exits_after_fill(
                    symbol=sym,
                    side=side,
                    df=df_live,
                    entry=entry,
                    sl=new_sl,
                    tp=999999999,   # pas de TP
                    size_lots=size_lots,
                    tp2=None,
                    update_only=True,
                )
                current_sl = new_sl
            except Exception as e:
                LOGGER.warning("[TRAIL] update SL failed %s: %s", sym, e)

        elif side == "sell" and new_sl < current_sl:
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

    LOGGER.info("[TRAIL] %s runner institutionnel terminé.", sym)


# ================================================================
# ORDER SENDER + FILL WATCHER (desk-grade)
# ================================================================
def _send_limit_order(
    sym: str,
    order_side: str,
    entry: float,
    size_lots: int,
    tick: float,
    sl: float,
    tp1: float,
    rr: float,
    sl_log: str,
    fp: str,
    df: pd.DataFrame,
):
    """
    Envoie un LIMIT maker → surveille fill → attache SL/TP1 →
    démarre trailing institutionnel.
    """

    # --- Envoi ordre initial ---
    order = place_limit_order(sym, order_side, entry, size_lots, post_only=True)
    if not order.get("ok"):
        body = order.get("data") or order.get("body") or {}
        code = str(body.get("code", ""))

        # Retry tick adjust
        if code == "300011":
            entry_retry = (
                _round_to_tick(entry - tick, tick)
                if order_side == "buy"
                else _round_to_tick(entry + tick, tick)
            )
            LOGGER.warning("Retry %s maker adjust (300011): %.8f -> %.8f",
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

    # --- Extraction orderId ---
    order_id = _extract_order_id(order)
    if not order_id:
        LOGGER.error("Réponse OK mais pas d'orderId : %s", order)
        unmark(fp)
        return None, None

    LOGGER.info("[ORDER] %s placed | id=%s | lots=%s | entry=%.8f",
                sym, order_id, size_lots, entry)

    # Telegram ACK
    try:
        from telegram_client import send_telegram_message
        send_telegram_message(
            f"🟡 Ordre placé {sym} ({order_side.upper()})\n"
            f"Lots: {size_lots} | Entry {entry:.6f}\n"
            f"SL prévu {sl:.6f} | TP1 {tp1:.6f} | RR {rr:.2f}"
        )
    except Exception:
        pass

    # ============================================================
    # WATCHER FILL (robuste desk-grade)
    # ============================================================
    def _monitor_fill_and_attach():
        try:
            t0 = time.time()
            max_seconds = float(FILL_MAX_HOURS) * 3600 if FILL_MAX_HOURS > 0 else None
            filled = False

            LOGGER.info("[FILL] watcher start %s order_id=%s side=%s",
                        sym, order_id, order_side)

            # ---- Poll loop ----
            while not filled:
                data = _get_order_status_flat(order_id)
                if data:
                    status = str(data.get("status", "")).lower()
                    deal = float(data.get("dealSize", 0.0))

                    LOGGER.debug("[FILL] %s poll status=%s deal=%s", sym, status, deal)

                    # ✔ Fill détecté par dealSize > 0
                    if deal > 0:
                        filled = True
                        break

                    # Annulation sans fill
                    if status in ("cancel", "canceled", "rejected") and deal <= 0:
                        LOGGER.info("[FILL] %s annulé sans fill", sym)
                        unmark(fp)
                        return

                # Fallback: position visible
                if _has_position(sym, order_side):
                    LOGGER.info("[FILL] %s détecté en position (fallback)", sym)
                    filled = True
                    break

                if max_seconds and (time.time() - t0) > max_seconds:
                    LOGGER.info("[FILL] %s timeout — exits NON posés", sym)
                    return

                time.sleep(FILL_POLL_SEC)

            # ---- Position visible ----
            _wait_until_position_visible(sym, order_side)

            # ---- Purge reduce-only existants ----
            try:
                purge_reduce_only(sym)
            except Exception:
                pass

            # ---- Pose SL + TP1 ----
            try:
                sl_r, tp_r = attach_exits_after_fill(
                    symbol=sym,
                    side=order_side,
                    df=df,
                    entry=entry,
                    sl=sl,
                    tp=tp1,
                    size_lots=size_lots,
                    tp2=None,
                )
                LOGGER.info("[EXITS] %s SL=%s | TP1=%s | %s",
                            sym, sl_r, tp_r, sl_log)
            except Exception as e:
                LOGGER.exception("attach_exits_after_fill failed %s: %s", sym, e)
                return

            # Telegram fill
            try:
                from telegram_client import send_telegram_message
                send_telegram_message(
                    f"✅ {sym} rempli ({order_side.upper()})\n"
                    f"Entry {entry:.6f} | SL {sl:.6f} | TP1 {tp1:.6f} | RR {rr:.2f}"
                )
            except Exception:
                pass

            # ========================================================
            #     LANCEMENT DU TRAILING INSTITUTIONNEL (Option 1)
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

    # Thread watcher
    threading.Thread(target=_monitor_fill_and_attach, daemon=True).start()

    return order_id, entry

# ================================================================
# BLOC 4/4 — Main Loop : Scan → Préparation → Order + Watcher
# ================================================================

def scan_and_send_signals():
    """
    Pipeline complet version C :
      1. Fetch symboles KuCoin
      2. Extract signal (advanced → fallback)
      3. SL/TP1 robustes + RR check
      4. Anti-doublons desk-grade
      5. Placement LIMIT maker
      6. Watcher Fill + Trailing Institutionnel
    """
    reset_scan_counters()
    pairs = fetch_all_symbols(limit=TOP_N_SYMBOLS)

    LOGGER.info("=== Start scan %d pairs ===", len(pairs))

    for idx, sym in enumerate(pairs, 1):
        try:
            # --------------------------
            # 1) Fetch OHLC 1H
            # --------------------------
            df = fetch_klines(sym, "1h", 300)
            if df.empty:
                LOGGER.info("[%d/%d] Skip %s (df empty)", idx, len(pairs), sym)
                continue

            # --------------------------
            # 2) Préparer le trade (Bloc 2)
            # --------------------------
            prepared = _prepare_trade(sym, df, idx, len(pairs))
            if prepared is None:
                continue

            (
                signal,
                res,
                entry,
                sl,
                tp1,
                rr,
                size_lots,
                order_side,
                tick,
                lot_mult,
                lot_step,
                fp,
                df_live,
            ) = (
                prepared["signal"],
                prepared["res"],
                prepared["entry"],
                prepared["sl"],
                prepared["tp1"],
                prepared["rr"],
                prepared["size_lots"],
                prepared["order_side"],
                prepared["tick"],
                prepared["lot_mult"],
                prepared["lot_step"],
                prepared["fp"],
                prepared["df"],
            )

            # --------------------------
            # 3) Guardrails Risk Manager
            # --------------------------
            notional = entry * lot_mult * size_lots
            ok, why = guardrails_ok(sym, notional)
            if not ok:
                LOGGER.info("[%d/%d] Skip %s -> %s", idx, len(pairs), sym, why)
                continue

            # --------------------------
            # 4) DRY RUN
            # --------------------------
            if DRY_RUN:
                LOGGER.info(
                    "[%d/%d] DRY %s lots=%s entry=%.6f sl=%.6f tp1=%.6f rr=%.2f",
                    idx,
                    len(pairs),
                    sym,
                    size_lots,
                    entry,
                    sl,
                    tp1,
                    rr,
                )
                register_order(sym, notional)
                continue

            # --------------------------
            # 5) SEND ORDER + WATCHER (Bloc 3)
            # --------------------------
            order_id, live_entry = _send_limit_order(
                sym=sym,
                order_side=order_side,
                entry=entry,
                size_lots=size_lots,
                tick=tick,
                sl=sl,
                tp1=tp1,
                rr=rr,
                sl_log=signal["sl_log"],
                fp=fp,
                df=df_live,
            )

            if order_id:
                register_order(sym, notional)

            time.sleep(0.4)

        except Exception as e:
            LOGGER.exception("Error on %s: %s", sym, e)

    LOGGER.info("=== Scan done ===")
