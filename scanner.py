"""
scanner.py — version avec logs détaillés et fallback
- Log du nombre de paires
- Si aucune paire, on réessaie le fetch une fois puis on scanne un fallback minimal
"""
import time
import logging
from kucoin_utils import fetch_all_symbols, fetch_klines
from analyze_signal import evaluate_signal
from kucoin_trader import place_limit_order
from telegram_client import send_telegram_message

LOGGER = logging.getLogger(__name__)
_FALLBACK = ["XBTUSDTM", "ETHUSDTM", "SOLUSDTM", "BNBUSDTM"]

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

def _symbols_with_fallback() -> list[str]:
    pairs = fetch_all_symbols()
    if not pairs:
        LOGGER.warning("Aucune paire récupérée, nouvelle tentative...")
        time.sleep(1.0)
        pairs = fetch_all_symbols()
    if not pairs:
        LOGGER.error("Toujours 0 paire — utilisation du fallback statique")
        pairs = _FALLBACK
    return pairs

def scan_and_send_signals():
    pairs = _symbols_with_fallback()
    LOGGER.info("Start scan %d pairs", len(pairs))
    for sym in pairs:
        try:
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
                send_telegram_message("✅ " + msg)
                price = float(df["close"].iloc[-1])
                side = "buy" if signal["bias"] == "LONG" else "sell"
                order = place_limit_order(sym, side, price)
                LOGGER.info("Order %s %s @%.8f -> %s", sym, side, price, order)
            else:
                send_telegram_message("❌ " + msg)

            time.sleep(0.8)  # anti-429
        except Exception as e:
            LOGGER.exception("Error on %s: %s", sym, e)

    LOGGER.info("Scan done.")
