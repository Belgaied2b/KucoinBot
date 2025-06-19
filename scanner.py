import json
import time
import pandas as pd
from kucoin_utils import fetch_all_symbols, fetch_klines
from signal_analysis import analyze_signal
from telegram_utils import send_signal_to_telegram
from macro import get_macro_context

# Chargement des signaux déjà envoyés
try:
    with open("sent_signals.json", "r") as f:
        sent_signals = json.load(f)
except FileNotFoundError:
    sent_signals = {}

def save_sent_signal(symbol, direction):
    sent_signals[f"{symbol}_{direction}"] = time.time()
    with open("sent_signals.json", "w") as f:
        json.dump(sent_signals, f)

def already_sent(symbol, direction):
    return f"{symbol}_{direction}" in sent_signals

async def scan_and_send_signals():
    symbols = fetch_all_symbols()
    context_macro = get_macro_context()

    for symbol in symbols:
        for direction in ["long", "short"]:
            if already_sent(symbol, direction):
                continue

            df = fetch_klines(symbol, interval="1h", limit=200)
            if df is None or df.empty:
                continue

            df.name = symbol
            signal = analyze_signal(df, symbol=symbol, direction=direction, context_macro=context_macro)

            if signal:
                send_signal_to_telegram(signal)
                save_sent_signal(symbol, direction)
