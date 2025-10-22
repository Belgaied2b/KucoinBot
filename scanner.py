"""
scanner.py — Orchestration avec Pré-shoot + Corrélation + Risk corrélé
- Tente d'abord le moteur avancé (signal_engine) si activé
- Calcule le contexte inter-marchés (correlations.market_context)
- Si confluence insuffisante, tente un pré-shoot (preshoot.probability)
- Évalue via analyze_signal (RR/structure/momentum/institutionnel)
- Applique guardrails de base + guardrails corrélés (portfolio_risk)
- N'envoie sur Telegram que les VALIDES; DRY_RUN respecte ton réglage
"""
import time
import logging
import numpy as np
import pandas as pd

from kucoin_utils import fetch_all_symbols, fetch_klines, get_contract_info
from analyze_signal import evaluate_signal
from kucoin_trader import place_limit_order
from exits import place_stop_loss, place_take_profit
from settings import (
    DRY_RUN, TOP_N_SYMBOLS, ENABLE_SQUEEZE_ENGINE, FAIL_OPEN_TO_CORE
)
from risk_manager import reset_scan_counters, guardrails_ok, register_order, compute_vol_sizing
from correlations import market_context
from portfolio_risk import guardrails_ok_portfolio
from preshoot import preshoot_probability

LOGGER = logging.getLogger(__name__)

def _fmt(sym, res, extra: str = ""):
    inst = res.get("institutional", {})
    rr = res.get("rr", None)
    rr_txt = "n/a" if rr is None or not np.isfinite(rr) or rr <= 0 else f"{min(rr, 10.0):.2f}"
    return (f"🔎 *{sym}* — score {res.get('score',0):.1f} | RR {rr_txt}\n"
            f"Inst: {inst.get('institutional_score',0)}/3 ({inst.get('institutional_strength','?')}) — {inst.get('institutional_comment','')}"
            f"{extra}")

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

def _build_core(df, sym):
    # core safe signal (LONG par défaut)
    entry = float(df["close"].iloc[-1])
    atr = (df["high"] - df["low"]).rolling(14).mean().iloc[-1]
    if not np.isfinite(atr) or atr <= 0: atr = max(entry * 0.003, 1e-6)
    sl = entry - 1.5 * atr; tp = entry + 2.0 * atr
    stop = entry - sl; prof = tp - entry
    if stop <= 0 or prof <= 0: return None
    rr = prof / stop
    return {"symbol": sym, "bias": "LONG", "entry": entry, "sl": sl, "tp1": entry + prof/2, "tp2": tp,
            "rr_estimated": float(rr), "df": df, "ote": True}

def scan_and_send_signals():
    reset_scan_counters()
    pairs = fetch_all_symbols(limit=TOP_N_SYMBOLS)
    LOGGER.info("Start scan %d pairs", len(pairs))

    for idx, sym in enumerate(pairs, 1):
        try:
            df = fetch_klines(sym, "1h", 300)
            if df.empty:
                LOGGER.info("Skip %s (df empty)", sym); continue

            # Contexte inter-marchés (pour risk corrélé et filtrage)
            ctx = market_context(sym, df)

            # 1) Moteur avancé (si activé)
            signal, extra = _try_advanced(sym, df)
            # 2) Sinon core
            if signal is None:
                signal = _build_core(df, sym)
                if signal is None:
                    LOGGER.info("Skip %s -> core build failed", sym)
                    continue

            # 3) Si advanced absent/insuffisant, tenter un PRE-SHOOT opportuniste
            if extra == "":
                prob, early = preshoot_probability(sym, df)
                if prob >= 0.7 and early and early["rr"] >= 1.3:
                    # remplace l'entry pack par le early pack (anticipation)
                    signal["entry"], signal["sl"], signal["tp2"], signal["rr_estimated"] = early["entry"], early["sl"], early["tp"], early["rr"]
                    extra = f"\nPre-shoot p={prob:.2f} | RR {early['rr']:.2f}"

            # 4) Évalue le signal (institutionnel/structure/momentum/RR)
            res = evaluate_signal(signal)
            if not res["valid"]:
                LOGGER.info("[%d/%d] Skip %s -> %s", idx, len(pairs), sym, ", ".join(res.get("reasons") or []))
                continue

            # 5) Sizing volatilité
            meta = get_contract_info(sym)
            sizing = compute_vol_sizing(
                df=df, entry_price=signal["entry"], sl_price=signal["sl"],
                lot_multiplier=float(meta.get("multiplier", 1.0)),
                lot_size_min=int(meta.get("lotSize", 1)),
                tick_size=float(meta.get("tickSize", 0.01)),
            )

            # 6) Guardrails de base
            ok, why = guardrails_ok(sym, sizing.notional)
            if not ok:
                LOGGER.info("[%d/%d] Skip %s -> %s", idx, len(pairs), sym, why); continue

            # 7) Guardrails corrélés (par groupe)
            okp, why2 = guardrails_ok_portfolio(sym, sizing.notional, ctx)
            if not okp:
                LOGGER.info("[%d/%d] Skip %s -> %s", idx, len(pairs), sym, why2); continue

            msg = _fmt(sym, res, extra)

            if DRY_RUN:
                LOGGER.info("[%d/%d] DRY-RUN %s lots=%s entry=%.6f sl=%.6f tp=%.6f | ctx=%s",
                            idx, len(pairs), sym, sizing.size_lots, sizing.price_rounded, signal["sl"], signal["tp2"], ctx)
                register_order(sym, sizing.notional)
            else:
                order = place_limit_order(sym, "buy", sizing.price_rounded)
                LOGGER.info("[%d/%d] Order %s -> %s", idx, len(pairs), sym, order)
                from telegram_client import send_telegram_message
                send_telegram_message("✅ " + msg + f"\nLots: {sizing.size_lots} | Entry {sizing.price_rounded:.6f} | SL {signal['sl']:.6f} | TP {signal['tp2']:.6f}")
                register_order(sym, sizing.notional)
                sl_resp = place_stop_loss(sym, "buy", sizing.size_lots, signal["sl"])
                tp_resp = place_take_profit(sym, "buy", sizing.size_lots, signal["tp2"])
                LOGGER.info("Exits %s -> SL %s | TP %s", sym, sl_resp, tp_resp)

            time.sleep(0.7)
        except Exception as e:
            LOGGER.exception("Error on %s: %s", sym, e)

    LOGGER.info("Scan done.")
