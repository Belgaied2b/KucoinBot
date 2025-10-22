"""
scanner.py — orchestration avec exits APRES fill
- Conserve ta logique (confluence/évaluation).
- Envoie l'entrée, attend un (début de) fill, purge anciens exits, pose SL/TP.
- Envoie Telegram seulement pour les trades valides (comme avant).
"""
from __future__ import annotations
import time, logging, numpy as np, pandas as pd

from kucoin_utils import fetch_all_symbols, fetch_klines, get_contract_info
from analyze_signal import evaluate_signal
from settings import DRY_RUN, TOP_N_SYMBOLS, ENABLE_SQUEEZE_ENGINE, FAIL_OPEN_TO_CORE
from risk_manager import reset_scan_counters, guardrails_ok, register_order, compute_vol_sizing
from kucoin_trader import place_limit_order
from exits_manager import purge_reduce_only, attach_exits_after_fill
from fills import wait_for_fill

LOGGER = logging.getLogger(__name__)

def _fmt(sym, res, extra: str = ""):
    inst = res.get("institutional", {})
    rr = res.get("rr", None)
    rr_txt = "n/a" if rr is None or not np.isfinite(rr) or rr <= 0 else f"{min(rr, 10.0):.2f}"
    return (f"🔎 *{sym}* — score {res.get('score',0):.1f} | RR {rr_txt}\n"
            f"Inst: {inst.get('institutional_score',0)}/3 ({inst.get('institutional_strength','?')}) — {inst.get('institutional_comment','')}{extra}")

def _build_core(df, sym):
    entry = float(df["close"].iloc[-1])
    atr = (df["high"] - df["low"]).rolling(14).mean().iloc[-1]
    if not np.isfinite(atr) or atr <= 0: atr = max(entry * 0.003, 1e-6)
    sl = entry - 1.5 * atr; tp = entry + 2.0 * atr
    stop = entry - sl; prof = tp - entry
    if stop <= 0 or prof <= 0: return None
    rr = prof / stop
    return {"symbol": sym, "bias": "LONG", "entry": entry, "sl": sl, "tp1": entry + prof/2, "tp2": tp,
            "rr_estimated": float(rr), "df": df, "ote": True}

def _try_advanced(sym, df):
    if not ENABLE_SQUEEZE_ENGINE: return None, ""
    try:
        from signal_engine import generate_trade_candidate
        sig, err, dbg = generate_trade_candidate(sym, df)
        if err: return None, ""
        extra = f"\nConfluence {dbg.get('conf','?')} | ADX {dbg.get('adx','?'):.1f} | HV% {dbg.get('hvp','?'):.0f} | Squeeze {dbg.get('sq','?')}"
        return sig, extra
    except Exception as e:
        LOGGER.exception("Advanced engine error on %s: %s", sym, e)
        return (None, "") if FAIL_OPEN_TO_CORE else (None, "BLOCK")

def scan_and_send_signals():
    reset_scan_counters()
    pairs = fetch_all_symbols(limit=TOP_N_SYMBOLS)
    LOGGER.info("Start scan %d pairs", len(pairs))

    for idx, sym in enumerate(pairs, 1):
        try:
            df = fetch_klines(sym, "1h", 300)
            if df.empty:
                LOGGER.info("Skip %s (df empty)", sym); continue

            signal, extra = _try_advanced(sym, df)
            if signal is None:
                signal = _build_core(df, sym)
                if signal is None:
                    LOGGER.info("Skip %s -> core build failed", sym); continue

            res = evaluate_signal(signal)
            if not res["valid"]:
                LOGGER.info("[%d/%d] Skip %s -> %s", idx, len(pairs), sym, ", ".join(res.get("reasons") or []))
                continue

            meta = get_contract_info(sym)
            sizing = compute_vol_sizing(
                df=df, entry_price=signal["entry"], sl_price=signal["sl"],
                lot_multiplier=float(meta.get("multiplier", 1.0)),
                lot_size_min=int(meta.get("lotSize", 1)),
                tick_size=float(meta.get("tickSize", 0.01)),
            )
            ok, why = guardrails_ok(sym, sizing.notional)
            if not ok:
                LOGGER.info("[%d/%d] Skip %s -> %s", idx, len(pairs), sym, why); continue

            msg = _fmt(sym, res, extra)

            if DRY_RUN:
                LOGGER.info("[%d/%d] DRY-RUN %s lots=%s entry=%.6f sl=%.6f tp=%.6f",
                            idx, len(pairs), sym, sizing.size_lots, sizing.price_rounded, signal["sl"], signal["tp2"])
                register_order(sym, sizing.notional)
            else:
                # 1) envoyer l'entrée
                order = place_limit_order(sym, "buy", sizing.price_rounded, sizing.size_lots, post_only=False)
                LOGGER.info("[%d/%d] Order %s -> %s", idx, len(pairs), sym, order)

                order_id = ((order.get("data") or {}).get("data") or {}).get("orderId")
                if not order_id:
                    LOGGER.error("No orderId returned for %s, skip exits.", sym)
                    continue

                # 2) attendre un (début de) fill
                fill = wait_for_fill(order_id, timeout_s=20)
                if not fill["filled"]:
                    LOGGER.info("No fill yet on %s — exits delayed", sym)
                    # Option: envoyer un message Telegram "en attente de fill"
                    # from telegram_client import send_telegram_message
                    # send_telegram_message("🕒 En attente de fill " + msg)
                    continue

                # 3) purge des anciens exits reduce-only
                purge_reduce_only(sym)

                # 4) pose des exits maintenant que la position existe
                sl_resp, tp_resp = attach_exits_after_fill(sym, "buy", signal["df"],
                                                           signal["entry"], signal["sl"], signal["tp2"],
                                                           sizing.size_lots)
                LOGGER.info("Exits %s -> SL %s | TP %s", sym, sl_resp, tp_resp)

                # 5) Telegram (succès)
                from telegram_client import send_telegram_message
                send_telegram_message("✅ " + msg + f"\nLots: {sizing.size_lots} | Entry {sizing.price_rounded:.6f} | SL {signal['sl']:.6f} | TP {signal['tp2']:.6f}")

                register_order(sym, sizing.notional)

            time.sleep(0.6)
        except Exception as e:
            LOGGER.exception("Error on %s: %s", sym, e)

    LOGGER.info("Scan done.")
