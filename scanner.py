import time, logging
from kucoin_utils import fetch_all_symbols, fetch_klines
from analyze_signal import evaluate_signal
from kucoin_trader import place_limit_order
from telegram_client import send_telegram_message
from settings import SCAN_INTERVAL_MIN

LOGGER = logging.getLogger(__name__)

def _fmt_signal_msg(sym, res):
    inst = res["institutional"]
    return (
        f"🔎 *{sym}* — score {res['score']:.1f} | RR {res['rr']:.2f}\n"
        f"Inst: {inst['institutional_score']}/3 ({inst['institutional_strength']}) — {inst['institutional_comment']}\n"
        f"Notes: {', '.join(res['reasons']) if res['reasons'] else 'OK'}"
    )

def scan_and_send_signals():
    pairs = fetch_all_symbols()
    LOGGER.info("Start scan %d pairs", len(pairs))
    for sym in pairs:
        try:
            df = fetch_klines(sym, "1h", 200)
            if df.empty: 
                LOGGER.warning("No data for %s", sym); continue
            # Exemple de construction de signal; adapte avec ta logique d’entrée / SL / TP.
            signal = {"symbol": sym, "bias": "LONG", "rr_estimated": 1.8, "df": df, "ote": True}
            res = evaluate_signal(signal)
            if res["valid"]:
                send_telegram_message("✅ " + _fmt_signal_msg(sym, res))
                price = df["close"].iloc[-1]
                side = "buy" if signal["bias"]=="LONG" else "sell"
                order = place_limit_order(sym, side, price)
                LOGGER.info("Order %s %s @%s -> %s", sym, side, price, order)
            else:
                send_telegram_message("❌ " + _fmt_signal_msg(sym, res))
            time.sleep(0.8)  # anti-429 APIs publiques
        except Exception as e:
            LOGGER.exception("Error on %s: %s", sym, e)
    LOGGER.info("Scan done.")
