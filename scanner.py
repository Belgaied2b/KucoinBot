"""
scanner.py — orchestration avec exits APRES fill + stops robustes + sizing par risque + garde-fous KuCoin
- Stops: True ATR + swing + buffer, RR validé (+ meta [EXITS]).
- Taille: par risque ($) puis cap par marge (MARGIN_USDT & LEVERAGE).
- Entrée: OTE gate + LIMIT maker (ladder) sécurisé (tick) + retry si 300011 (anti “market déguisé”).
- Exits: posés APRES fill, purge des anciens reduce-only.
- TP1/TP2 (nouveau): 1.5R / 2.5R avec alignement structurel (swing).
- Break-Even (nouveau): déplacement automatique du SL à l'entrée lorsque TP1 est atteint.
- Anti-doublons: empreinte persistante + garde position/ordres ouverts (même côté).
- Day stop & exposure guard (si modules présents).
"""
from __future__ import annotations
import time, logging, numpy as np, pandas as pd

from kucoin_utils import fetch_all_symbols, fetch_klines, get_contract_info
from analyze_signal import evaluate_signal
from settings import (
    DRY_RUN, TOP_N_SYMBOLS, ENABLE_SQUEEZE_ENGINE, FAIL_OPEN_TO_CORE,
    RISK_USDT, RR_TARGET, LEVERAGE, MARGIN_USDT,
)

from risk_manager import reset_scan_counters, guardrails_ok, register_order
from kucoin_trader import place_limit_order, get_open_position, list_open_orders
from exits_manager import purge_reduce_only, attach_exits_after_fill
from fills import wait_for_fill

# nouveaux helpers
from stops import protective_stop_long, protective_stop_short, format_sl_meta_for_log
from sizing import lots_by_risk
from duplicate_guard import signal_fingerprint, is_duplicate_and_mark, unmark

# quick wins
from ote_utils import compute_ote_zone  # OTE gate auto
from ladder import build_ladder_prices  # ladder maker

# optionnels (fail-safe si absents)
try:
    from day_guard import day_guard_ok
except Exception:
    def day_guard_ok(): return True, "no_day_guard"

try:
    from exposure_guard import exposure_ok
    # on suppose qu’un inventaire des expositions est disponible côté risk_manager (optionnel)
    try:
        from risk_manager import get_open_notional_by_symbol
    except Exception:
        def get_open_notional_by_symbol(): return {}
except Exception:
    exposure_ok = None
    def get_open_notional_by_symbol(): return {}

LOGGER = logging.getLogger(__name__)

# -------------------------- utilitaires locaux --------------------------
def _fmt(sym, res, extra: str = ""):
    inst = res.get("institutional", {})
    rr = res.get("rr", None)
    rr_txt = "n/a" if rr is None or not np.isfinite(rr) or rr <= 0 else f"{min(rr, 10.0):.2f}"
    return (f"🔎 *{sym}* — score {res.get('score',0):.1f} | RR {rr_txt}\n"
            f"Inst: {inst.get('institutional_score',0)}/3 ({inst.get('institutional_strength','?')}) — {inst.get('institutional_comment','')}{extra}")

def _round_to_tick(x: float, tick: float) -> float:
    if tick <= 0: return float(x)
    steps = int(float(x)/float(tick))
    return round(steps * float(tick), 8)

def _validate_rr_and_fix(bias: str, entry: float, sl: float, tp: float, tick: float) -> tuple[bool, float, float, float, float]:
    """
    Renvoie (ok, entry, sl, tp, rr). Corrige les cas 'borderline' (SL trop proche, mauvais côté).
    """
    min_tick_gap = max(3 * tick, entry * 0.001)  # au moins 0.1% ou 3 ticks
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

    rr = reward / risk
    return (rr >= 1.05), entry, sl, tp, rr  # 1.05 = garde-fou minimum pour considérer cohérent

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

# ----------- TP engine : 1.5R / 2.5R + alignement swing (institutionnel) -----------
def _swing_levels(df: pd.DataFrame, lookback: int = 20) -> tuple[float, float]:
    swing_high = float(df["high"].rolling(lookback).max().iloc[-2])
    swing_low  = float(df["low"].rolling(lookback).min().iloc[-2])
    return swing_high, swing_low

def _compute_tp_levels(df: pd.DataFrame, entry: float, sl: float, bias: str,
                       rr1: float = 1.5, rr2: float = 2.5,
                       tick: float = 0.01, lookback: int = 20) -> tuple[float, float]:
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
def _build_core(df, sym):
    entry = float(df["close"].iloc[-1])
    return {"symbol": sym, "bias": "LONG", "entry": entry, "df": df, "ote": True}

def _try_advanced(sym, df):
    if not ENABLE_SQUEEZE_ENGINE:
        return None, ""
    try:
        from signal_engine import generate_trade_candidate
        sig, err, dbg = generate_trade_candidate(sym, df)
        if err:
            return None, ""
        extra = f"\nConfluence {dbg.get('conf','?')} | ADX {dbg.get('adx','?'):.1f} | HV% {dbg.get('hvp','?'):.0f} | Squeeze {dbg.get('sq','?')}"
        return sig, extra
    except Exception as e:
        LOGGER.exception("Advanced engine error on %s: %s", sym, e)
        return (None, "") if FAIL_OPEN_TO_CORE else (None, "BLOCK")

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
                # tolérance 1 tick
                in_ote = (low - tick) <= last <= (high + tick)
                # construit une petite ladder dans la zone OTE (3 niveaux)
                side = "buy" if bias == "LONG" else "sell"
                ladder_prices = build_ladder_prices(side, low, high, tick, n=3) or [entry]
                # choisit le niveau “cheap” (premier côté cheap de la ladder)
                entry = float(ladder_prices[0])
            else:
                side = "buy" if bias == "LONG" else "sell"

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
                LOGGER.info("[%d/%d] Skip %s -> RR invalide (entry/SL/TP incohérents)", idx, len(pairs), sym)
                continue

            # Taille par risque puis cap par marge
            size_lots = lots_by_risk(entry, sl, lot_mult, lot_step, float(RISK_USDT))
            size_lots = _cap_by_margin(entry, lot_mult, lot_step, size_lots)
            if size_lots < lot_step:
                LOGGER.info("[%d/%d] Skip %s -> taille insuffisante après cap marge", idx, len(pairs), sym)
                continue

            # === Notionnel & Marge estimée (DEBUG lisible avec exposure_guard en "marge") ===
            notional = entry * lot_mult * size_lots
            est_margin = notional / max(float(LEVERAGE), 1.0)

            # Day guard
            ok_day, why_day = day_guard_ok()
            if not ok_day:
                LOGGER.info("[%d/%d] Skip %s -> day guard: %s", idx, len(pairs), sym, why_day)
                continue

            # Exposure guard (si dispo) — compare côté guard en MARGE (conversion interne)
            if exposure_ok is not None:
                try:
                    open_notional_by_symbol = get_open_notional_by_symbol() or {}
                    ok_exp, why_exp = exposure_ok(open_notional_by_symbol, sym, notional, float(RISK_USDT))
                    if not ok_exp:
                        LOGGER.info("[%d/%d] Skip %s -> exposure guard: %s | est_margin=%.0f | notional=%.0f",
                                    idx, len(pairs), sym, why_exp, est_margin, notional)
                        continue
                    else:
                        LOGGER.info("[EXPO] %s OK | est_margin=%.0f | notional=%.0f", sym, est_margin, notional)
                except Exception as e:
                    LOGGER.debug("Exposure guard unavailable: %s", e)

            # Evaluation (règles desk pro)
            signal.update({
                "entry": entry,
                "sl": sl,
                "tp1": tp1_raw,   # pour monitoring BE
                "tp2": tp2,       # utilisé pour l’exit réel
                "size_lots": size_lots,
                "bias": bias,
                "rr_estimated": rr,
                "sl_log": sl_log
            })
            res = evaluate_signal(signal)
            if not res.get("valid"):
                LOGGER.info("[%d/%d] Skip %s -> %s", idx, len(pairs), sym, ", ".join(res.get("reasons") or []))
                continue

            ok, why = guardrails_ok(sym, notional)
            if not ok:
                LOGGER.info("[%d/%d] Skip %s -> %s", idx, len(pairs), sym, why)
                continue

            # ===========================
            #     ANTI-DOUBLONS (NEW)
            # ===========================
            tf = "1h"  # cohérent avec fetch_klines

            # 1) Garde position existante (même côté)
            try:
                pos = get_open_position(sym) or {}
                qty = float(pos.get("currentQty") or 0.0)
                pos_side = str(pos.get("side") or "").lower()
                if qty > 0 and pos_side == side:
                    LOGGER.info("[DUP] %s %s déjà en position (qty=%s) → skip signal", sym, side, qty)
                    time.sleep(0.2)
                    continue
            except Exception:
                pass

            # 2) Garde ordre LIMIT ouvert (non reduce-only) même côté
            try:
                open_o = list_open_orders(sym) or []
                has_same_side_limit = False
                for o in open_o:
                    o_side = (o.get("side") or "").lower()
                    otype = (o.get("type") or o.get("orderType") or "").lower()
                    ro = str(o.get("reduceOnly")).lower() == "true"
                    if (o_side == side) and ("limit" in otype) and not ro:
                        has_same_side_limit = True
                        break
                if has_same_side_limit:
                    LOGGER.info("[DUP] %s %s a déjà un LIMIT ouvert → skip signal", sym, side)
                    time.sleep(0.2)
                    continue
            except Exception:
                pass

            # 3) Empreinte persistante (structure + zone d'entrée bucketisée)
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
                side=side,
                timeframe=tf,
                entry_price=float(entry),
                tick_size=tick,
                entry_bucket_ticks=10,  # considérés identiques si même zone ±10 ticks
                structure=structure,
            )

            # 3.a Lecture sans marquage : si présent → skip
            if is_duplicate_and_mark(fp, ttl_seconds=6*3600, mark=False):
                LOGGER.info("[DUP] %s %s empreinte déjà présente → skip signal", sym, side)
                time.sleep(0.2)
                continue

            msg = _fmt(sym, res, extra)

            if DRY_RUN:
                # marquage pour éviter re-spam même en dry-run (optionnel)
                is_duplicate_and_mark(fp, ttl_seconds=6*3600, mark=True)

                LOGGER.info("[%d/%d] DRY-RUN %s lots=%s entry=%.6f sl=%.6f tp1=%.6f tp2=%.6f rr=%.2f | [EXITS] %s",
                            idx, len(pairs), sym, size_lots, entry, sl, tp1_raw, tp2, rr, sl_log)
                register_order(sym, notional)
            else:
                # 3.b Marquer juste avant d'agir (évite courses)
                race = is_duplicate_and_mark(fp, ttl_seconds=6*3600, mark=True)
                if race is True:
                    LOGGER.info("[DUP] %s %s condition de course (déjà marqué) → skip", sym, side)
                    time.sleep(0.2)
                    continue

                # 1) envoyer l'entrée (LIMIT maker) + retry si 300011
                #    NB: on force post_only=True pour éviter le market déguisé.
                def _extract_order_id(resp: dict) -> str | None:
                    if not resp:
                        return None
                    for path in [
                        ["data", "data", "orderId"],
                        ["data", "orderId"],
                        ["orderId"],
                        ["data", "order_id"],
                    ]:
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

                order = place_limit_order(sym, side, entry, size_lots, post_only=True)
                if not order.get("ok"):
                    body = (order.get("body") or {})
                    code = str((body or {}).get("code"))
                    if code == "300011":
                        # prix non maker → on se décale d’1 tick “cheap”
                        entry_retry = _round_to_tick(entry - tick, tick) if side == "buy" else _round_to_tick(entry + tick, tick)
                        LOGGER.warning("Retry %s maker adjust for code 300011: %.8f -> %.8f", sym, entry, entry_retry)
                        order = place_limit_order(sym, side, entry_retry, size_lots, post_only=True)
                        if order.get("ok"):
                            entry = entry_retry
                        else:
                            unmark(fp)  # libère pour retenter plus tard
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
                LOGGER.info("[%d/%d] Order %s -> %s | order_id=%s", idx, len(pairs), sym, order, order_id)

                # --- Telegram immédiat: ACK de l'ordre placé (même si pas encore rempli)
                try:
                    from telegram_client import send_telegram_message
                    send_telegram_message(
                        f"🟡 Ordre placé {sym} ({side.upper()})\n"
                        f"Lots: {size_lots} | Entry {entry:.6f}\n"
                        f"SL prévisionnel {sl:.6f} | TP1 {tp1_raw:.6f} | TP2 {tp2:.6f}\n"
                        f"(SL/TP seront posés dès le fill)"
                    )
                except Exception:
                    pass

                # --- Worker asynchrone: attend le fill, pose SL/TP, lance BE, envoie Telegram OK
                def _monitor_fill_and_attach():
                    try:
                        # 2) attendre le fill (fenêtre plus large, ex: 10 minutes)
                        fill = {"filled": False}
                        try:
                            f1 = wait_for_fill(order_id, timeout_s=30) if order_id else {"filled": False}
                            fill = f1 if isinstance(f1, dict) else {"filled": False}
                        except Exception:
                            fill = {"filled": False}

                        # Polling si pas encore rempli
                        t0 = time.time()
                        timeout_total = 10 * 60  # 10 minutes
                        poll_every = 5.0
                        while not fill.get("filled") and (time.time() - t0) < timeout_total:
                            time.sleep(poll_every)
                            try:
                                f2 = wait_for_fill(order_id, timeout_s=1) if order_id else {"filled": False}
                                fill = f2 if isinstance(f2, dict) else {"filled": False}
                            except Exception:
                                # fallback: si position apparue, on considère "rempli"
                                try:
                                    pos = get_open_position(sym) or {}
                                    qty = float(pos.get("currentQty") or 0.0)
                                    pos_side = str(pos.get("side") or "").lower()
                                    if qty > 0 and pos_side == side:
                                        fill = {"filled": True}
                                        break
                                except Exception:
                                    pass

                        if not fill.get("filled"):
                            LOGGER.info("No fill yet on %s — exits delayed (worker timeout) | [EXITS] %s", sym, sl_log)
                            return  # on stoppe le worker; la marque anti-dup reste en place

                        # 3) purge des anciens exits reduce-only
                        try:
                            purge_reduce_only(sym)
                        except Exception as e:
                            LOGGER.warning("purge_reduce_only failed for %s: %s", sym, e)

                        # 4) pose des exits maintenant que la position existe (SL/TP)
                        try:
                            sl_resp, tp_resp = attach_exits_after_fill(
                                symbol=sym,
                                side=side,
                                df=df,
                                entry=entry,
                                sl=sl,
                                tp=tp1_raw,       # TP1 principal
                                size_lots=size_lots,
                                tp2=tp2           # TP2 secondaire
                            )
                            LOGGER.info("Exits %s -> SL %s | TP %s | [EXITS] %s", sym, sl_resp, tp_resp, sl_log)
                        except Exception as e:
                            LOGGER.exception("attach_exits_after_fill failed on %s: %s", sym, e)
                            # on tente au moins d'annoncer le fill
                            try:
                                from telegram_client import send_telegram_message
                                send_telegram_message(
                                    f"🟢 {sym} rempli ({side.upper()}) — SL/TP non posés (erreur)."
                                )
                            except Exception:
                                pass
                            return

                        # 5) Lancer le moniteur Break-Even (déplacement SL à BE à TP1)
                        try:
                            from breakeven_manager import monitor_breakeven
                            import threading
                            threading.Thread(
                                target=monitor_breakeven,
                                args=(sym, side, entry, tp1_raw, tp2, size_lots),
                                daemon=True
                            ).start()
                        except Exception as e:
                            LOGGER.warning("BE monitor failed for %s: %s", sym, e)

                        # 6) Telegram (succès final)
                        try:
                            from telegram_client import send_telegram_message
                            send_telegram_message(
                                f"✅ {sym} rempli ({side.upper()})\n"
                                f"Lots: {size_lots} | Entry {entry:.6f}\n"
                                f"SL {sl:.6f} | TP1 {tp1_raw:.6f} | TP2 {tp2:.6f} | RR {rr:.2f}\n"
                                f"[EXITS] {sl_log}"
                            )
                        except Exception:
                            pass
                    except Exception as e:
                        LOGGER.exception("monitor_fill_and_attach error on %s: %s", sym, e)

                # démarrage du worker asynchrone
                try:
                    import threading
                    threading.Thread(target=_monitor_fill_and_attach, daemon=True).start()
                except Exception as e:
                    LOGGER.warning("Unable to start fill worker for %s: %s", sym, e)

                # on ne bloque plus le loop : on passe au symbole suivant

            time.sleep(0.6)
        except Exception as e:
            LOGGER.exception("Error on %s: %s", sym, e)

    LOGGER.info("Scan done.")
