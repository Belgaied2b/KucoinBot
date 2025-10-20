"""
scanner.py
Scanne les paires, analyse les signaux, envoie sur Telegram et exécute KuCoin.
"""
import logging, time
from analyze_signal import evaluate_signal
from kucoin_trader import place_limit_order
from kucoin_utils import fetch_all_symbols, fetch_klines

def scan_and_send_signals():
    pairs = fetch_all_symbols()
    for sym in pairs:
        df = fetch_klines(sym, "1h", 100)
        signal = {
            "symbol": sym,
            "bias": "LONG",
            "rr_estimated": 1.8
        }
        result = evaluate_signal(signal)
        if result["valid"]:
            print(f"✅ Signal {sym} | {result['comment']} | score {result['score']}")
            place_limit_order(sym, "buy", df["close"].iloc[-1])
        else:
            print(f"❌ Signal {sym} rejeté | {result['comment']}")
        time.sleep(1)
