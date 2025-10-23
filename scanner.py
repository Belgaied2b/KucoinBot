"""
scanner.py — orchestration avec exits APRES fill + stops robustes + sizing par risque + garde-fous KuCoin
- Stops: True ATR + swing + buffer, RR validé.
- Taille: par risque ($) puis cap par marge (MARGIN_USDT & LEVERAGE).
- Entrée: LIMIT sécurisée (tick) + retry si 300011.
- Exits: posés APRES fill, purge des anciens reduce-only.
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
from kucoin_trader import place_limit_order
from exits_manager import purge_reduce_only, attach_exits_after_fill
from fills import wait_for_fill

# nouveaux helpers (fichiers ajoutés)
from stops import protective_stop_long, protective_stop_short
from sizing import lots_by_risk

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
                dist = max(1e-8, entry - sl_raw)
                tp_raw = entry + RR_TARGET * dist
            else:
                sl_raw = protective_stop_short(df, entry, tick)
                dist = max(1e-8, sl_raw - entry)
                tp_raw = entry - RR_TARGET * dist

            ok_rr, entry, sl, tp, rr = _validate_rr_and_fix(bias, entry, sl_raw, tp_raw, tick)
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
            signal.update({"entry": entry, "sl": sl, "tp2": tp, "size_lots": size_lots, "bias": bias, "rr_estimated": rr})
            res = evaluate_signal(signal)
            if not res.get("valid"):
                LOGGER.info("[%d/%d] Skip %s -> %s", idx, len(pairs), sym, ", ".join(res.get("reasons") or []))
                continue

            ok, why = guardrails_ok(sym, notional)
            if not ok:
                LOGGER.info("[%d/%d] Skip %s -> %s", idx, len(pairs), sym, why)
                continue

            msg = _fmt(sym, res, extra)

            if DRY_RUN:
                LOGGER.info("[%d/%d] DRY-RUN %s lots=%s entry=%.6f sl=%.6f tp=%.6f rr=%.2f",
                            idx, len(pairs), sym, size_lots, entry, sl, tp, rr)
                register_order(sym, notional)
            else:
                side = "buy" if bias == "LONG" else "sell"

                # 1) envoyer l'entrée (LIMIT) + retry si 300011
                order = place_limit_order(sym, side, entry, size_lots, post_only=False)
                if not order.get("ok"):
                    body = (order.get("body") or {})
                    code = str((body or {}).get("code"))
                    if code == "300011":
                        # price out of range -> ajuste le prix et retente UNE fois
                        adj = 0.995 if side == "buy" else 1.005
                        entry_retry = _round_to_tick(entry * adj, tick)
                        LOGGER.warning("Retry %s price adjust for code 300011: %.8f -> %.8f", sym, entry, entry_retry)
                        order = place_limit_order(sym, side, entry_retry, size_lots, post_only=False)
                        entry = entry_retry  # garde trace du prix d'entrée visé
                    elif code == "300003":
                        LOGGER.error("Balance insuffisante pour %s (marge requise). Skip.", sym)
                        # pas d'exits, on passe au suivant
                        time.sleep(0.2)
                        continue

                LOGGER.info("[%d/%d] Order %s -> %s", idx, len(pairs), sym, order)
                order_id = ((order.get("data") or {}).get("data") or {}).get("orderId")
                if not order_id:
                    LOGGER.error("No orderId returned for %s, skip exits.", sym)
                    continue

                # 2) attendre un (début de) fill
                fill = wait_for_fill(order_id, timeout_s=20)
                if not fill["filled"]:
                    LOGGER.info("No fill yet on %s — exits delayed", sym)
                    continue

                # 3) purge des anciens exits reduce-only
                purge_reduce_only(sym)

                # 4) pose des exits maintenant que la position existe
                sl_resp, tp_resp = attach_exits_after_fill(sym, side, df, entry, sl, tp, size_lots)
                LOGGER.info("Exits %s -> SL %s | TP %s", sym, sl_resp, tp_resp)

                # 5) Telegram (succès)
                try:
                    from telegram_client import send_telegram_message
                    send_telegram_message(
                        "✅ " + msg +
                        f"\nSide: {side.upper()} | Lots: {size_lots} | Entry {entry:.6f} | SL {sl:.6f} | TP {tp:.6f} | RR {rr:.2f}"
                    )
                except Exception:
                    pass

                register_order(sym, notional)

            time.sleep(0.6)
        except Exception as e:
            LOGGER.exception("Error on %s: %s", sym, e)

    LOGGER.info("Scan done.")
