"""
scanner.py — orchestration avec exits APRES fill + stops robustes + sizing par risque + garde-fous KuCoin
- Stops: True ATR + swing + buffer, RR validé.
- Taille: par risque ($) puis cap par marge (MARGIN_USDT & LEVERAGE).
- Entrée: LIMIT sécurisée (tick) + retry si 300011.
- Exits: posés APRES fill, purge des anciens reduce-only.
- TP1/TP2 (nouveau): 1.5R / 2.5R avec alignement structurel (swing).
- Break-Even (nouveau): déplacement automatique du SL à l'entrée lorsque TP1 est atteint.
- Anti-doublons: empreinte persistante + garde position/ordres ouverts (même côté).
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

# nouveaux helpers (fichiers ajoutés)
from stops import protective_stop_long, protective_stop_short
from sizing import lots_by_risk

# anti-doublons (nouveau fichier duplicate_guard.py requis)
from duplicate_guard import signal_fingerprint, is_duplicate_and_mark, unmark

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

            if bias == "LONG":
                sl_raw = protective_stop_long(df, entry, tick)
            else:
                sl_raw = protective_stop_short(df, entry, tick)

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

            notional = entry * lot_mult * size_lots

            # Evaluation (garde tes règles)
            signal.update({
                "entry": entry,
                "sl": sl,
                "tp1": tp1_raw,   # pour monitoring BE
                "tp2": tp2,       # utilisé pour l’exit réel
                "size_lots": size_lots,
                "bias": bias,
                "rr_estimated": rr
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
            side = "buy" if bias == "LONG" else "sell"
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

                LOGGER.info("[%d/%d] DRY-RUN %s lots=%s entry=%.6f sl=%.6f tp1=%.6f tp2=%.6f rr=%.2f",
                            idx, len(pairs), sym, size_lots, entry, sl, signal["tp1"], signal["tp2"], rr)
                register_order(sym, notional)
            else:
                # 3.b Marquer juste avant d'agir (évite courses)
                race = is_duplicate_and_mark(fp, ttl_seconds=6*3600, mark=True)
                # Note: la fonction retourne False en cas de marquage réussi dans notre implémentation
                # mais on gère défensivement : si elle renvoie True, on traite comme doublon.
                if race is True:
                    LOGGER.info("[DUP] %s %s condition de course (déjà marqué) → skip", sym, side)
                    time.sleep(0.2)
                    continue

                # 1) envoyer l'entrée (LIMIT) + retry si 300011
                order = place_limit_order(sym, side, entry, size_lots, post_only=False)
                if not order.get("ok"):
                    body = (order.get("body") or {})
                    code = str((body or {}).get("code"))
                    if code == "300011":
                        adj = 0.995 if side == "buy" else 1.005
                        entry_retry = _round_to_tick(entry * adj, tick)
                        LOGGER.warning("Retry %s price adjust for code 300011: %.8f -> %.8f", sym, entry, entry_retry)
                        order = place_limit_order(sym, side, entry_retry, size_lots, post_only=False)
                        if order.get("ok"):
                            entry = entry_retry
                        else:
                            # échec => on libère le fingerprint pour laisser une chance ultérieure
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

                LOGGER.info("[%d/%d] Order %s -> %s", idx, len(pairs), sym, order)
                order_id = ((order.get("data") or {}).get("data") or {}).get("orderId")
                if not order_id:
                    LOGGER.error("No orderId returned for %s, skip exits.", sym)
                    # ordre non confirmé → on dé-marque pour ne pas bloquer indéfiniment
                    unmark(fp)
                    continue

                # 2) attendre un (début de) fill
                fill = wait_for_fill(order_id, timeout_s=20)
                if not fill["filled"]:
                    LOGGER.info("No fill yet on %s — exits delayed", sym)
                    # Ici on garde la marque pour éviter les re-ordres en boucle pendant le TTL
                    continue

                # 3) purge des anciens exits reduce-only
                purge_reduce_only(sym)

                # 4) pose des exits maintenant que la position existe (SL initial + TP2)
                sl_resp, tp_resp = attach_exits_after_fill(sym, side, df, entry, sl, signal["tp2"], size_lots)
                LOGGER.info("Exits %s -> SL %s | TP %s", sym, sl_resp, tp_resp)

                # 5) Lancer le moniteur Break-Even (déplacement SL à BE à TP1)
                try:
                    from breakeven_manager import monitor_breakeven
                    import threading
                    threading.Thread(
                        target=monitor_breakeven,
                        args=(sym, side, entry, signal["tp1"], signal["tp2"], size_lots),
                        daemon=True
                    ).start()
                except Exception as e:
                    LOGGER.warning("BE monitor failed for %s: %s", sym, e)

                # 6) Telegram (succès)
                try:
                    from telegram_client import send_telegram_message
                    send_telegram_message(
                        "✅ " + msg +
                        f"\nSide: {side.upper()} | Lots: {size_lots} | Entry {entry:.6f} | SL {sl:.6f} | TP1 {signal['tp1']:.6f} | TP2 {signal['tp2']:.6f} | RR {rr:.2f}"
                    )
                except Exception:
                    pass

                register_order(sym, notional)

            time.sleep(0.6)
        except Exception as e:
            LOGGER.exception("Error on %s: %s", sym, e)

    LOGGER.info("Scan done.")
