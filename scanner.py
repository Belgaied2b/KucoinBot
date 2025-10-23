"""
scanner.py — orchestration avec exits APRES fill + stops robustes + sizing par risque
- Conserve ta logique (confluence/évaluation).
- Calcule SL/TP = structure & ATR (robuste) ; taille = risque $ (RISK_USDT).
- Envoie l'entrée, attend un (début de) fill, purge anciens exits, pose SL/TP.
- Envoie Telegram seulement pour les trades valides (comme avant).
"""
from __future__ import annotations
import time, logging, numpy as np, pandas as pd

from kucoin_utils import fetch_all_symbols, fetch_klines, get_contract_info
from analyze_signal import evaluate_signal
from settings import (
    DRY_RUN, TOP_N_SYMBOLS, ENABLE_SQUEEZE_ENGINE, FAIL_OPEN_TO_CORE,
    RISK_USDT, RR_TARGET,
)
from risk_manager import reset_scan_counters, guardrails_ok, register_order
from kucoin_trader import place_limit_order
from exits_manager import purge_reduce_only, attach_exits_after_fill
from fills import wait_for_fill

# nouveaux helpers (fichiers à avoir ajoutés auparavant)
from stops import protective_stop_long, protective_stop_short
from sizing import lots_by_risk

LOGGER = logging.getLogger(__name__)

# --------------------------------- Utils ---------------------------------
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

# Core fallback (si moteur avancé indisponible)
def _build_core(df, sym):
    entry = float(df["close"].iloc[-1])
    # petit core: on laisse le sizing/SL/TP au pipeline principal
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

# ------------------------------- Main scan --------------------------------
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

            # ----------------- Construction SL/TP robustes + sizing par risque -----------------
            meta = get_contract_info(sym)
            tick = float(meta.get("tickSize", 0.01))
            lot_mult = float(meta.get("multiplier", 1.0))
            lot_step = int(meta.get("lotSize", 1))

            # entry = dernière clôture (tu peux remplacer par mark si dispo ailleurs)
            entry = float(signal.get("entry") or df["close"].iloc[-1])
            entry = _round_to_tick(entry, tick)

            bias = (signal.get("bias") or "LONG").upper()
            if bias not in ("LONG", "SHORT"):
                bias = "LONG"

            if bias == "LONG":
                sl = protective_stop_long(df, entry, tick)
                risk_dist = max(1e-8, entry - sl)
                tp = entry + RR_TARGET * risk_dist
                side = "buy"
            else:  # SHORT
                sl = protective_stop_short(df, entry, tick)
                risk_dist = max(1e-8, sl - entry)
                tp = entry - RR_TARGET * risk_dist
                side = "sell"

            tp = _round_to_tick(tp, tick)
            sl = _round_to_tick(sl, tick)

            # Taille par risque ($) -> nombre de lots
            size_lots = lots_by_risk(entry, sl, lot_mult, lot_step, float(RISK_USDT))
            if size_lots < lot_step:
                LOGGER.info("[%d/%d] Skip %s -> risk sizing too small (lots=%s)", idx, len(pairs), sym, size_lots)
                continue

            # Notional pour les guardrails (approx par lot: entry * multiplier)
            notional = entry * lot_mult * size_lots

            # Évaluation du signal (garde ta logique) — injecte les valeurs calculées
            signal.update({"entry": entry, "sl": sl, "tp2": tp, "size_lots": size_lots, "bias": bias})
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
                LOGGER.info("[%d/%d] DRY-RUN %s lots=%s entry=%.6f sl=%.6f tp=%.6f",
                            idx, len(pairs), sym, size_lots, entry, sl, tp)
                register_order(sym, notional)
            else:
                # 1) envoyer l'entrée (compat 3 ou 4 args)
                order = place_limit_order(sym, side, entry, size_lots, post_only=False)
                LOGGER.info("[%d/%d] Order %s -> %s", idx, len(pairs), sym, order)

                order_id = ((order.get("data") or {}).get("data") or {}).get("orderId")
                if not order_id:
                    LOGGER.error("No orderId returned for %s, skip exits.", sym)
                    continue

                # 2) attendre un (début de) fill
                fill = wait_for_fill(order_id, timeout_s=20)
                if not fill["filled"]:
                    LOGGER.info("No fill yet on %s — exits delayed", sym)
                    # Option: Telegram "en attente de fill"
                    # from telegram_client import send_telegram_message
                    # send_telegram_message("🕒 En attente de fill " + msg)
                    continue

                # 3) purge des anciens exits reduce-only
                purge_reduce_only(sym)

                # 4) pose des exits maintenant que la position existe
                sl_resp, tp_resp = attach_exits_after_fill(
                    sym, side, df, entry, sl, tp, size_lots
                )
                LOGGER.info("Exits %s -> SL %s | TP %s", sym, sl_resp, tp_resp)

                # 5) Telegram (succès)
                try:
                    from telegram_client import send_telegram_message
                    send_telegram_message(
                        "✅ " + msg +
                        f"\nSide: {side.upper()} | Lots: {size_lots} | Entry {entry:.6f} | SL {sl:.6f} | TP {tp:.6f}"
                    )
                except Exception:
                    pass

                register_order(sym, notional)

            time.sleep(0.6)
        except Exception as e:
            LOGGER.exception("Error on %s: %s", sym, e)

    LOGGER.info("Scan done.")
