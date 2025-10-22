import time
import logging
from kucoin_utils import fetch_all_symbols, fetch_klines
from analyze_signal import evaluate_signal
from kucoin_trader import place_limit_order
from telegram_client import send_telegram_message
from settings import MAX_ORDERS_PER_SCAN, DRY_RUN

LOGGER = logging.getLogger(__name__)

TOP_N = 150  # déjà en place

def _fmt_signal_msg(sym, res):
    inst = res.get("institutional", {})
    if isinstance(inst, dict) and "institutional_score" in inst:
        inst_line = f"{inst['institutional_score']}/3 ({inst.get('institutional_strength','?')}) — {inst.get('institutional_comment','')}"
    else:
        inst_line = "n/a"
    reasons = res.get("reasons") or []
    return (
        f"🔎 *{sym}* — score {res.get('score',0):.1f} | RR {res.get('rr',0):.2f}\n"
        f"Inst: {inst_line}\n"
        f"Notes: {', '.join(reasons) if reasons else 'OK'}"
    )

def scan_and_send_signals():
    pairs = fetch_all_symbols(limit=TOP_N)
    LOGGER.info("Start scan %d pairs", len(pairs))

    sent_orders = 0
    for idx, sym in enumerate(pairs, start=1):
        try:
            if sent_orders >= MAX_ORDERS_PER_SCAN:
                LOGGER.info("Order cap reached (%d). Remaining symbols will be skipped.", MAX_ORDERS_PER_SCAN)
                break

            df = fetch_klines(sym, "1h", 200)
            if df.empty:
                LOGGER.warning("Skip %s (df empty)", sym)
                continue

            # Exemple de signal de base (adapte à ta logique d'entrée/SL/TP)
            signal = {
                "symbol": sym,
                "bias": "LONG",
                "rr_estimated": 1.8,
                "df": df,
                "ote": True
            }

            res = evaluate_signal(signal)
            msg = _fmt_signal_msg(sym, res)

            if res.get("valid"):
                if DRY_RUN:
                    send_telegram_message("🧪 (DRY-RUN) " + msg)
                    LOGGER.info("[%d/%d] DRY-RUN %s -> NO ORDER", idx, len(pairs), sym)
                else:
                    price = float(df["close"].iloc[-1])
                    side = "buy" if signal["bias"] == "LONG" else "sell"
                    order = place_limit_order(sym, side, price)
                    sent_orders += 1
                    LOGGER.info("[%d/%d] Order %s %s @%.8f -> %s", idx, len(pairs), sym, side, price, order)
                    send_telegram_message("✅ " + msg)
            else:
                LOGGER.info("[%d/%d] Skip %s -> %s", idx, len(pairs), sym, "; ".join(res.get("reasons") or []))
                send_telegram_message("❌ " + msg)

            # Anti-429 KuCoin/Binance/Telegram
            time.sleep(0.6)

        except Exception as e:
            LOGGER.exception("Error on %s: %s", sym, e)

    LOGGER.info("Scan done. Orders sent this run: %d", sent_orders)
