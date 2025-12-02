"""
scanner.py — orchestration avec exits APRES fill + stops robustes + sizing par risque + garde-fous KuCoin
- Stops: True ATR + swing + buffer, RR validé (+ meta [EXITS]).
- Taille: par risque ($) puis cap par marge (MARGIN_USDT & LEVERAGE).
- Entrée: OTE gate + LIMIT maker (ladder) sécurisé (tick) + retry si 300011 (anti “market déguisé”).
- Exits: posés APRES fill, purge des anciens reduce-only.
- TP1/TP2 : 1.5R / 2.5R avec alignement structurel (swing).
- Break-Even : déplacement automatique du SL à l'entrée lorsque TP1 est atteint.
- Anti-doublons: empreinte persistante + garde position/ordres ouverts (même côté).
- Exposure guard en MARGE (converti depuis notionnel).
- Desk pro: watcher continu du fill, attache SL/TP/BE dès exécution, gère annulations réelles (status KuCoin).
"""
from __future__ import annotations
import time
import logging
import threading
from typing import Optional

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
    RR_TARGET,          # gardé pour compat
    LEVERAGE,
    MARGIN_USDT,
)

# (optionnels) contrôles du watcher depuis settings.py
try:
    from settings import FILL_POLL_SEC
except Exception:
    FILL_POLL_SEC = 10  # secondes

try:
    from settings import FILL_MAX_HOURS
except Exception:
    FILL_MAX_HOURS = 0  # 0 = illimité (desk pro)

from risk_manager import reset_scan_counters, guardrails_ok, register_order
from kucoin_trader import (
    place_limit_order,
    get_open_position,
    list_open_orders,
    get_order as get_order_status,  # unifié ici
)
from exits_manager import purge_reduce_only, attach_exits_after_fill

from stops import protective_stop_long, protective_stop_short, format_sl_meta_for_log
from sizing import lots_by_risk
from duplicate_guard import signal_fingerprint, is_duplicate_and_mark, unmark

from ote_utils import compute_ote_zone  # OTE gate auto
from ladder import build_ladder_prices  # ladder maker

# optionnel : fast-path via ancien module fills, sinon no-op
try:
    from fills import wait_for_fill
except Exception:  # pragma: no cover
    def wait_for_fill(order_id: str, timeout_s: int = 20):
        return {"filled": False}

# optionnels (fail-safe si absents)
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

LOGGER = logging.getLogger(__name__)

# -------------------------- utilitaires locaux --------------------------
def _fmt(sym, res, extra: str = ""):
    inst = res.get("institutional", {})
    rr = res.get("rr", None)
    rr_txt = "n/a" if rr is None or not np.isfinite(rr) or rr <= 0 else f"{min(rr, 10.0):.2f}"
    return (
        f"🔎 *{sym}* — score {res.get('score', 0):.1f} | RR {rr_txt}\n"
        f"Inst: {inst.get('institutional_score', 0)}/3 ({inst.get('institutional_strength', '?')}) — "
        f"{inst.get('institutional_comment', '')}{extra}"
    )

def _round_to_tick(x: float, tick: float) -> float:
    if tick <= 0:
        return float(x)
    steps = int(float(x) / float(tick))
    return round(steps * float(tick), 8)

def _validate_rr_and_fix(
    bias: str, entry: float, sl: float, tp: float, tick: float
) -> tuple[bool, float, float, float, float]:
    """
    Renvoie (ok, entry, sl, tp, rr).
    Corrige SL/TP géométriquement, laisse le filtre RR strict à analyze_signal.
    """
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

def _cap_by_margin(entry: float, lot_mult: float, lot_step: int, size_lots: int) -> int:
    """
    Cap la taille pour que la marge requise <= MARGIN_USDT.
    marge_req = (entry * lot_mult * lots) / LEVERAGE
    """
    if LEVERAGE <= 0:
        return size_lots
    max_lots_margin = int((MARGIN_USDT * LEVERAGE) // max(1e-12, entry * lot_mult))
    max_lots_margin -= (max_lots_margin % max(1, lot_step))
    return max(lot_step, min(size_lots, max_lots_margin))

# ----------- TP engine : 1.5R / 2.5R + alignement swing -----------
def _swing_levels(df: pd.DataFrame, lookback: int = 20) -> tuple[float, float]:
    swing_high = float(df["high"].rolling(lookback).max().iloc[-2])
    swing_low = float(df["low"].rolling(lookback).min().iloc[-2])
    return swing_high, swing_low

def _compute_tp_levels(
    df: pd.DataFrame,
    entry: float,
    sl: float,
    bias: str,
    rr1: float = 1.5,
    rr2: float = 2.5,
    tick: float = 0.01,
    lookback: int = 20,
) -> tuple[float, float]:
    swing_high, swing_low = _swing_levels(df, lookback)
    if bias == "LONG":
        risk = max(1e-8, entry - sl)
        tp1 = entry + rr1 * risk
        tp2_rr = entry + rr2 * risk
        tp2 = swing_high if (swing_high > entry and swing_high < tp2_rr) else tp2_rr
    else:
        risk = max(1e-8, sl - entry)
        tp1 = entry - rr1 * risk
        tp2_rr = entry - rr2 * risk
        tp2 = swing_low if (swing_low < entry and swing_low > tp2_rr) else tp2_rr
    return _round_to_tick(tp1, tick), _round_to_tick(tp2, tick)

# ------------------------------- Core fallbacks -------------------------------
def _build_core(df: pd.DataFrame, sym: str):
    entry = float(df["close"].iloc[-1])
    return {"symbol": sym, "bias": "LONG", "entry": entry, "df": df, "ote": True}

def _try_advanced(sym: str, df: pd.DataFrame):
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

# ------------------------------- Watcher Fill (desk pro) -------------------------------
def _extract_order_id(resp: dict) -> Optional[str]:
    """
    Essaye d'extraire l'orderId de la réponse KuCoin.
    Compatible avec place_limit_order (kucoin_trader).
    """
    if not resp:
        return None
    for path in [["data", "data", "orderId"], ["data", "orderId"], ["orderId"], ["data", "order_id"]]:
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
    Récupère le dict ordre via kucoin_trader.get_order(order_id).
    - get_order retourne déjà data->dict avec status/dealSize
    - fallback si jamais une couche "data" supplémentaire existe.
    """
    if not order_id:
        return {}
    try:
        d = get_order_status(order_id) or {}
        if isinstance(d, dict) and d.get("status") is not None:
            return d
        data = d.get("data") if isinstance(d, dict) else {}
        if isinstance(data, dict):
            return data
        return {}
    except Exception as e:
        LOGGER.debug("get_order_status_flat error %s: %s", order_id, e)
        return {}

def _has_position(sym: str, side: str) -> bool:
    """
    Fallback robust : regarde la position ouverte et mappe long/short -> buy/sell.
    """
    try:
        pos = get_open_position(sym) or {}
        qty = float(pos.get("currentQty") or 0.0)
        raw_side = str(pos.get("side") or "").lower()
        if raw_side in ("buy", "long"):
            pos_side = "buy"
        elif raw_side in ("sell", "short"):
            pos_side = "sell"
        else:
            pos_side = raw_side
        side_norm = (side or "").lower()
        LOGGER.debug("[FILL-FB] %s position qty=%s side_raw=%s side_norm=%s", sym, qty, raw_side, pos_side)
        return qty > 0 and pos_side == side_norm
    except Exception as e:
        LOGGER.debug("_has_position error on %s: %s", sym, e)
        return False

def _wait_until_position_visible(sym: str, side: str, timeout_s: float = 5.0) -> bool:
    """
    Après détection du fill, on attend que la position soit réellement visible
    côté KuCoin pour éviter les erreurs 300009 ('No open positions to close')
    au moment de poser les ordres reduce-only.
    """
    t0 = time.time()
    while time.time() - t0 < timeout_s:
        if _has_position(sym, side):
            LOGGER.info("[FILL] %s position visible (side=%s)", sym, side)
            return True
        time.sleep(0.5)
    LOGGER.warning("[FILL] %s position PAS visible après %.1fs (side=%s)", sym, timeout_s, side)
    return False

# --------------------------------- Main loop ---------------------------------
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

            signal, extra = _try_advanced(sym, df)
            if signal is None:
                signal = _build_core(df, sym)
                if signal is None:
                    LOGGER.info("Skip %s -> core build failed", sym)
                    continue

            # ---- Construction SL/TP robustes + sizing par risque ----
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

            # --- Gate OTE (H1 swing propre) + ladder maker ---
            ote_zone = None
            try:
                ote_zone = compute_ote_zone(df, bias, lb=60)  # (low, high)
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

            # --- SL avec meta + log [EXITS] ---
            if bias == "LONG":
                sl_val, sl_meta = protective_stop_long(df, entry, tick, return_meta=True)
            else:
                sl_val, sl_meta = protective_stop_short(df, entry, tick, return_meta=True)
            sl_raw = float(sl_val)
            sl_log = format_sl_meta_for_log(sl_meta)
            LOGGER.info("[EXITS][SL] %s | entry=%.12f | sl=%.12f", sl_log, entry, sl_raw)

            # --- TP1/TP2 (1.5R/2.5R + alignement swing) ---
            tp1_raw, tp2_raw = _compute_tp_levels(df, entry, sl_raw, bias, rr1=1.5, rr2=2.5, tick=tick, lookback=20)

            ok_rr, entry, sl, tp2, rr = _validate_rr_and_fix(bias, entry, sl_raw, tp2_raw, tick)
            if not ok_rr:
                LOGGER.info(
                    "[%d/%d] Skip %s -> RR invalide (entry/SL/TP incohérents)",
                    idx,
                    len(pairs),
                    sym,
                )
                continue

            # Taille par risque puis cap par marge
            size_lots = lots_by_risk(entry, sl, lot_mult, lot_step, float(RISK_USDT))
            size_lots = _cap_by_margin(entry, lot_mult, lot_step, size_lots)
            if size_lots < lot_step:
                LOGGER.info(
                    "[%d/%d] Skip %s -> taille insuffisante après cap marge",
                    idx,
                    len(pairs),
                    sym,
                )
                continue

            notional = entry * lot_mult * size_lots
            est_margin = notional / max(float(LEVERAGE), 1.0)

            # Day guard
            ok_day, why_day = day_guard_ok()
            if not ok_day:
                LOGGER.info(
                    "[%d/%d] Skip %s -> day guard: %s",
                    idx,
                    len(pairs),
                    sym,
                    why_day,
                )
                continue

            # Exposure guard
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
                            idx,
                            len(pairs),
                            sym,
                            why_exp,
                            est_margin,
                            notional,
                        )
                        continue
                    else:
                        LOGGER.info(
                            "[EXPO] %s OK | est_margin=%.0f | notional=%.0f",
                            sym,
                            est_margin,
                            notional,
                        )
                except Exception as e:
                    LOGGER.debug("Exposure guard unavailable: %s", e)

            # Evaluation finale
            signal.update(
                {
                    "entry": entry,
                    "sl": sl,
                    "tp1": tp1_raw,
                    "tp2": tp2,
                    "size_lots": size_lots,
                    "bias": bias,
                    "rr_estimated": rr,
                    "sl_log": sl_log,
                }
            )
            res = evaluate_signal(signal)
            if not res.get("valid"):
                LOGGER.info(
                    "[%d/%d] Skip %s -> %s",
                    idx,
                    len(pairs),
                    sym,
                    ", ".join(res.get("reasons") or []),
                )
                continue

            ok, why = guardrails_ok(sym, notional)
            if not ok:
                LOGGER.info(
                    "[%d/%d] Skip %s -> %s",
                    idx,
                    len(pairs),
                    sym,
                    why,
                )
                continue

            # ===========================
            #     ANTI-DOUBLONS
            # ===========================
            tf = "1h"

            # 1) Position existante (même côté)
            try:
                pos = get_open_position(sym) or {}
                qty = float(pos.get("currentQty") or 0.0)
                raw_side = str(pos.get("side") or "").lower()
                pos_side = "buy" if raw_side in ("buy", "long") else "sell" if raw_side in ("sell", "short") else raw_side
                if qty > 0 and pos_side == order_side:
                    LOGGER.info("[DUP] %s déjà en position (qty=%s side=%s) → skip", sym, qty, raw_side)
                    time.sleep(0.2)
                    continue
            except Exception:
                pass

            # 2) LIMIT déjà ouvert (non reduce-only) même côté
            try:
                open_o = list_open_orders(sym) or []
                has_same_side_limit = False
                for o in open_o:
                    o_side = (o.get("side") or "").lower()
                    otype = (o.get("type") or o.get("orderType") or "").lower()
                    ro = str(o.get("reduceOnly")).lower() == "true"
                    if (o_side == order_side) and ("limit" in otype) and not ro:
                        has_same_side_limit = True
                        break
                if has_same_side_limit:
                    LOGGER.info("[DUP] %s a déjà un LIMIT ouvert → skip", sym)
                    time.sleep(0.2)
                    continue
            except Exception:
                pass

            # 3) Empreinte persistante
            structure = {
                "bos_direction": res.get("bos_direction") or signal.get("bos_direction"),
                "choch_direction": res.get("choch_direction") or signal.get("choch_direction"),
                "has_liquidity_zone": res.get("has_liquidity_zone") or signal.get("has_liquidity_zone"),
                "trend": res.get("trend") or signal.get("trend"),
                "ema_state": res.get("ema_state") or signal.get("ema_state"),
                "tf": tf,
                "tf_confirm": res.get("tf_confirm") or signal.get("tf_confirm"),
                "mode": res.get("mode") or signal.get("mode"),
                "engulfing": res.get("engulfing") or signal.get("engulfing"),
                "fvg": res.get("fvg") or signal.get("fvg"),
                "cos": res.get("cos") or signal.get("cos"),
            }

            fp = signal_fingerprint(
                symbol=sym,
                side=order_side,
                timeframe=tf,
                entry_price=float(entry),
                tick_size=tick,
                entry_bucket_ticks=10,
                structure=structure,
            )

            if is_duplicate_and_mark(fp, ttl_seconds=6 * 3600, mark=False):
                LOGGER.info("[DUP] %s empreinte déjà présente → skip", sym)
                time.sleep(0.2)
                continue

            # ===========================
            #      ENVOI / WATCHER
            # ===========================
            if DRY_RUN:
                is_duplicate_and_mark(fp, ttl_seconds=6 * 3600, mark=True)
                LOGGER.info(
                    "[%d/%d] DRY-RUN %s lots=%s entry=%.6f sl=%.6f tp1=%.6f tp2=%.6f rr=%.2f | [EXITS] %s",
                    idx,
                    len(pairs),
                    sym,
                    size_lots,
                    entry,
                    sl,
                    tp1_raw,
                    tp2,
                    rr,
                    sl_log,
                )
                register_order(sym, notional)
            else:
                race = is_duplicate_and_mark(fp, ttl_seconds=6 * 3600, mark=True)
                if race is True:
                    LOGGER.info("[DUP] %s condition de course (déjà marqué) → skip", sym)
                    time.sleep(0.2)
                    continue

                # 1) envoyer l'entrée (LIMIT maker) + retry si 300011
                order = place_limit_order(sym, order_side, entry, size_lots, post_only=True)
                if not order.get("ok"):
                    body = (order.get("data") or order.get("body") or {})
                    code = str((body or {}).get("code"))
                    if code == "300011":
                        entry_retry = (
                            _round_to_tick(entry - tick, tick)
                            if order_side == "buy"
                            else _round_to_tick(entry + tick, tick)
                        )
                        LOGGER.warning(
                            "Retry %s maker adjust for code 300011: %.8f -> %.8f",
                            sym,
                            entry,
                            entry_retry,
                        )
                        order = place_limit_order(sym, order_side, entry_retry, size_lots, post_only=True)
                        if order.get("ok"):
                            entry = entry_retry
                        else:
                            unmark(fp)
                            time.sleep(0.2)
                            continue
                    elif code == "300003":
                        LOGGER.error("Balance insuffisante pour %s (marge requise). Skip.", sym)
                        unmark(fp)
                        time.sleep(0.2)
                        continue
                    else:
                        LOGGER.error("place_limit_order failed for %s: %s", sym, body)
                        unmark(fp)
                        time.sleep(0.2)
                        continue

                order_id = _extract_order_id(order)
                if not order_id:
                    LOGGER.error(
                        "[%d/%d] Order %s réponse ok MAIS pas d'orderId détecté -> on annule le trade côté bot. Resp=%s",
                        idx,
                        len(pairs),
                        sym,
                        order,
                    )
                    try:
                        from telegram_client import send_telegram_message
                        send_telegram_message(
                            f"⚪️ KuCoin a répondu OK pour {sym} mais aucun orderId détecté.\n"
                            f"L'ordre n'est PAS considéré comme placé par le bot. Check manuel conseillé."
                        )
                    except Exception:
                        pass
                    unmark(fp)
                    time.sleep(0.2)
                    continue

                LOGGER.info(
                    "[%d/%d] Order %s placed | order_id=%s | lots=%s | entry=%.8f | notional=%.2f | est_margin=%.2f",
                    idx,
                    len(pairs),
                    sym,
                    order_id,
                    size_lots,
                    entry,
                    notional,
                    est_margin,
                )

                # Telegram immédiat: ACK
                try:
                    from telegram_client import send_telegram_message

                    send_telegram_message(
                        f"🟡 Ordre placé {sym} ({order_side.upper()})\n"
                        f"Lots: {size_lots} | Entry {entry:.6f}\n"
                        f"SL prévisionnel {sl:.6f} | TP1 {tp1_raw:.6f} | TP2 {tp2:.6f}\n"
                        f"(SL/TP seront posés dès le fill)"
                    )
                except Exception:
                    pass

                # --- Watcher continu (desk pro) ---
                def _monitor_fill_and_attach():
                    """
                    Watcher unique par ordre:
                    - se base sur get_order(order_id) pour status / dealSize
                    - fallback get_open_position(sym) si nécessaire
                    - attend que la position soit bien visible avant de poser SL/TP
                    """
                    try:
                        t0 = time.time()
                        max_seconds = float(FILL_MAX_HOURS) * 3600.0 if float(FILL_MAX_HOURS) > 0 else None
                        filled = False

                        LOGGER.info(
                            "[FILL] watcher start %s order_id=%s side=%s",
                            sym, order_id, order_side
                        )

                        # === Boucle de surveillance du remplissage ===
                        while not filled:

                            # --- A) Status direct de l’ordre ---
                            if order_id:
                                data = _get_order_status_flat(order_id)

                                if data:
                                    status = str(data.get("status") or "").lower()
                                    deal_size = float(data.get("dealSize") or 0.0)
                                    remain = float(data.get("remainSize") or 0.0)

                                    LOGGER.debug(
                                        "[FILL] poll %s order_id=%s status=%s dealSize=%s remainSize=%s",
                                        sym, order_id, status, deal_size, remain
                                    )

                                    # Détection fill
                                    if deal_size > 0.0 and status in (
                                        "done", "match", "filled",
                                        "partialfill", "partialfilled",
                                    ):
                                        LOGGER.info(
                                            "[FILL] %s order_id=%s détecté rempli (status=%s, dealSize=%s)",
                                            sym, order_id, status, deal_size
                                        )
                                        filled = True
                                        break

                                    # Détection annulation
                                    if status in ("cancelled", "canceled", "cancel", "reject", "rejected") and deal_size <= 0.0:
                                        LOGGER.info(
                                            "[FILL] %s order_id=%s annulé (pas de fill)",
                                            sym, order_id
                                        )
                                        try:
                                            from telegram_client import send_telegram_message
                                            send_telegram_message(
                                                f"⚪️ Ordre annulé {sym} ({order_side.upper()}) — pas de fill.\n"
                                                f"Entry {entry:.6f} | Lots {size_lots}"
                                            )
                                        except Exception:
                                            pass

                                        try:
                                            unmark(fp)
                                        except Exception:
                                            pass

                                        return  # STOP watcher ici

                            # --- B) Fallback via position ---
                            if _has_position(sym, order_side):
                                LOGGER.info(
                                    "[FILL] %s détecté en position via get_open_position (fallback)",
                                    sym
                                )
                                filled = True
                                break

                            # Timeout éventuel
                            if max_seconds is not None and (time.time() - t0) > max_seconds:
                                LOGGER.info(
                                    "[FILL] %s watcher timeout (%sh) — exits NON posés",
                                    sym, FILL_MAX_HOURS
                                )
                                return

                            time.sleep(max(1.0, float(FILL_POLL_SEC)))

                        # === On attend que la position soit bien visible ===
                        _wait_until_position_visible(sym, order_side, timeout_s=5.0)

                        # === Attach exits ===
                        try:
                            purge_reduce_only(sym)
                        except Exception as e:
                            LOGGER.warning("purge_reduce_only failed for %s: %s", sym, e)

                        try:
                            sl_resp, tp_resp = attach_exits_after_fill(
                                symbol=sym,
                                side=order_side,
                                df=df,
                                entry=entry,
                                sl=sl,
                                tp=tp1_raw,
                                size_lots=size_lots,
                                tp2=tp2,
                            )
                            LOGGER.info(
                                "[EXITS] %s -> SL_resp=%s | TP1_resp=%s | meta=%s",
                                sym, sl_resp, tp_resp, sl_log
                            )
                        except Exception as e:
                            LOGGER.exception("attach_exits_after_fill failed on %s: %s", sym, e)
                            try:
                                from telegram_client import send_telegram_message
                                send_telegram_message(
                                    f"🟢 {sym} rempli ({order_side.upper()}) — SL/TP non posés (erreur interne)."
                                )
                            except Exception:
                                pass
                            return

                        # === Telegram succès final ===
                        try:
                            from telegram_client import send_telegram_message
                            send_telegram_message(
                                f"✅ {sym} rempli ({order_side.upper()})\n"
                                f"Lots: {size_lots} | Entry {entry:.6f}\n"
                                f"SL {sl:.6f} | TP1 {tp1_raw:.6f} | TP2 {tp2:.6f} | RR {rr:.2f}\n"
                                f"[EXITS] {sl_log}"
                            )
                        except Exception:
                            pass

                    except Exception as e:
                        LOGGER.exception("monitor_fill_and_attach error on %s: %s", sym, e)

                # démarrer le watcher (non bloquant)
                try:
                    threading.Thread(target=_monitor_fill_and_attach, daemon=True).start()
                except Exception as e:
                    LOGGER.warning("Unable to start fill watcher for %s: %s", sym, e)

                register_order(sym, notional)

            time.sleep(0.6)
        except Exception as e:
            LOGGER.exception("Error on %s: %s", sym, e)

    LOGGER.info("Scan done.")
