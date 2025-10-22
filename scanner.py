"""
scanner.py — orchestration Top-1
- Ne publie sur Telegram que les trades VALIDES
- Utilise le moteur de confluence 'signal_engine' pour construire des trades anticipés
"""
import time
import logging
import numpy as np
import pandas as pd

from kucoin_utils import fetch_all_symbols, fetch_klines, get_contract_info
from signal_engine import generate_trade_candidate
from analyze_signal import evaluate_signal
from kucoin_trader import place_limit_order
from exits import place_stop_loss, place_take_profit
from settings import DRY_RUN, TOP_N_SYMBOLS
from risk_manager import reset_scan_counters, guardrails_ok, register_order, compute_vol_sizing

LOGGER = logging.getLogger(__name__)

def _fmt_signal_msg(sym, res, dbg: dict | None = None):
    inst = res.get("institutional", {})
    inst_line = (
        f"{inst.get('institutional_score', 0)}/3 "
        f"({inst.get('institutional_strength', '?')}) — {inst.get('institutional_comment', '')}"
    )
    rr_val = res.get("rr", None)
    rr_txt = "n/a" if rr_val is None or not np.isfinite(rr_val) or rr_val <= 0 else f"{max(min(rr_val, 10.0), 0.0):.2f}"
    notes = ', '.join(res.get("reasons") or []) if res.get("reasons") else 'OK'
    extra = ""
    if dbg:
        extra = f"\nConfluence: {dbg.get('conf','?')} | ADX {dbg.get('adx','?'):.1f} | HV% {dbg.get('hvp','?'):.0f} | Squeeze {dbg.get('sq','?')}"
    return (
        f"🔎 *{sym}* — score {res.get('score', 0):.1f} | RR {rr_txt}\n"
        f"Inst: {inst_line}\n"
        f"Notes: {notes}{extra}"
    )

def scan_and_send_signals():
    reset_scan_counters()
    pairs = fetch_all_symbols(limit=TOP_N_SYMBOLS)
    LOGGER.info("Start scan %d pairs", len(pairs))

    for idx, sym in enumerate(pairs, 1):
        try:
            df = fetch_klines(sym, "1h", 300)
            if df.empty:
                LOGGER.info("Skip %s (df empty)", sym); 
                continue

            # Moteur pro: construit un trade anticipé si confluence suffisante
            signal, err, dbg = generate_trade_candidate(sym, df)
            if err:
                LOGGER.info("[%d/%d] Skip %s -> %s", idx, len(pairs), sym, err)
                continue

            # Évaluation institutionnelle/technique (R:R, structure, momentum…)
            res = evaluate_signal(signal)
            if not res["valid"]:
                LOGGER.info("[%d/%d] Skip %s -> %s", idx, len(pairs), sym, "; ".join(res.get("reasons") or []))
                continue

            # Sizing volatilité + gardes-fous portefeuille
            meta = get_contract_info(sym)
            sizing = compute_vol_sizing(
                df=df,
                entry_price=signal["entry"],
                sl_price=signal["sl"],
                lot_multiplier=float(meta.get("multiplier", 1.0)),
                lot_size_min=int(meta.get("lotSize", 1)),
                tick_size=float(meta.get("tickSize", 0.01)),
            )
            ok, why = guardrails_ok(sym, sizing.notional)
            if not ok:
                LOGGER.info("[%d/%d] Skip %s -> %s", idx, len(pairs), sym, why)
                continue

            # OK → envoi (ou DRY-RUN)
            msg = _fmt_signal_msg(sym, res, dbg)

            if DRY_RUN:
                LOGGER.info("[%d/%d] DRY-RUN %s lots=%s entry=%.6f sl=%.6f tp=%.6f",
                            idx, len(pairs), sym, sizing.size_lots, sizing.price_rounded, signal["sl"], signal["tp2"])
                register_order(sym, sizing.notional)
            else:
                order = place_limit_order(sym, "buy", sizing.price_rounded)
                LOGGER.info("[%d/%d] Order %s -> %s", idx, len(pairs), sym, order)
                from telegram_client import send_telegram_message
                send_telegram_message("✅ " + msg + f"\nLots: {sizing.size_lots} | Entry {sizing.price_rounded:.6f} | SL {signal['sl']:.6f} | TP {signal['tp2']:.6f}")
                register_order(sym, sizing.notional)
                # Exits
                sl_resp = place_stop_loss(sym, "buy", sizing.size_lots, signal["sl"])
                tp_resp = place_take_profit(sym, "buy", sizing.size_lots, signal["tp2"])
                LOGGER.info("Exits %s -> SL %s | TP %s", sym, sl_resp, tp_resp)

            time.sleep(0.7)
        except Exception as e:
            LOGGER.exception("Error on %s: %s", sym, e)

    LOGGER.info("Scan done.")
