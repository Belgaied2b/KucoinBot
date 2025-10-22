import time, logging
from kucoin_utils import fetch_all_symbols, fetch_klines, get_contract_info
from analyze_signal import evaluate_signal
from kucoin_trader import place_limit_order
from telegram_client import send_telegram_message
from settings import DRY_RUN
from risk_manager import (
    reset_scan_counters, guardrails_ok, register_order, compute_vol_sizing
)
from exits import place_stop_loss, place_take_profit

LOGGER = logging.getLogger(__name__)
TOP_N = 60  # on scanne moins, mieux

def _fmt(sym, res):
    inst = res["institutional"]; reasons = ", ".join(res["reasons"]) if res["reasons"] else "OK"
    return (f"🔎 *{sym}* — score {res['score']:.1f} | RR {res['rr']:.2f}\n"
            f"Inst: {inst['institutional_score']}/3 ({inst['institutional_strength']}) — {inst['institutional_comment']}\n"
            f"Notes: {reasons}")

def scan_and_send_signals():
    reset_scan_counters()
    pairs = fetch_all_symbols(limit=TOP_N)
    LOGGER.info("Start scan %d pairs", len(pairs))

    for idx, sym in enumerate(pairs, 1):
        try:
            df = fetch_klines(sym, "1h", 250)
            if df.empty:
                LOGGER.warning("Skip %s (df empty)", sym); continue

            # Exemple d’entry/SL/TP: tu peux brancher ta logique exacte ici
            entry = float(df["close"].iloc[-1])
            atr = (df["high"] - df["low"]).rolling(14).mean().iloc[-1]
            sl = entry - 1.5 * atr  # LONG
            tp = entry + 2.0 * atr

            signal = {"symbol": sym, "bias": "LONG", "rr_estimated": (tp-entry)/max(entry-sl,1e-9),
                      "df": df, "ote": True}

            res = evaluate_signal(signal)
            msg = _fmt(sym, res)

            if not res["valid"]:
                send_telegram_message("❌ " + msg)
                continue

            # Risk-first sizing & guardrails
            meta = get_contract_info(sym)
            sizing = compute_vol_sizing(df, entry, sl, meta.get("multiplier",1.0), int(meta.get("lotSize",1)), meta.get("tickSize",0.01))
            ok, why = guardrails_ok(sym, sizing.notional)
            if not ok:
                LOGGER.info("[%d/%d] Skip %s -> %s", idx, len(pairs), sym, why)
                send_telegram_message(f"⏭️ {sym} — {why}")
                continue

            if DRY_RUN:
                LOGGER.info("[%d/%d] DRY-RUN %s lots=%s entry=%.6f sl=%.6f tp=%.6f", idx, len(pairs), sym, sizing.size_lots, sizing.price_rounded, sl, tp)
                send_telegram_message("🧪 " + msg + f"\nLots: {sizing.size_lots} | Entry {sizing.price_rounded:.6f} | SL {sl:.6f} | TP {tp:.6f}")
                register_order(sym, sizing.notional)
                continue

            # Envoi ordre + exits attachés
            order = place_limit_order(sym, "buy", sizing.price_rounded)
            send_telegram_message("✅ " + msg + f"\nLots: {sizing.size_lots} | Entry {sizing.price_rounded:.6f} | SL {sl:.6f} | TP {tp:.6f}")
            LOGGER.info("[%d/%d] Order %s -> %s", idx, len(pairs), sym, order)
            register_order(sym, sizing.notional)

            # Place exits (best-effort)
            sl_resp = place_stop_loss(sym, "buy", sizing.size_lots, sl)
            tp_resp = place_take_profit(sym, "buy", sizing.size_lots, tp)
            LOGGER.info("Exits %s -> SL %s | TP %s", sym, sl_resp, tp_resp)

            time.sleep(0.7)
        except Exception as e:
            LOGGER.exception("Error on %s: %s", sym, e)

    LOGGER.info("Scan done.")
