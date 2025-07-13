import os
import json
import pandas as pd
import telegram
from datetime import datetime
from kucoin_utils import get_all_perp_symbols, get_klines
from signal_analysis import analyze_signal
from macros import load_macro_data
from utils import save_signal_to_csv, log_message

TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")
bot = telegram.Bot(token=TELEGRAM_TOKEN)

# Fichier de doublons
SENT_SIGNALS_FILE = "sent_signals.json"
if not os.path.exists(SENT_SIGNALS_FILE):
    with open(SENT_SIGNALS_FILE, "w") as f:
        json.dump([], f)

def load_sent_signals():
    with open(SENT_SIGNALS_FILE, "r") as f:
        return json.load(f)

def save_sent_signal(symbol, direction):
    data = load_sent_signals()
    data.append(f"{symbol}_{direction.upper()}")
    with open(SENT_SIGNALS_FILE, "w") as f:
        json.dump(data, f)

def already_sent(symbol, direction):
    return f"{symbol}_{direction.upper()}" in load_sent_signals()

def send_signal_to_telegram(result):
    caption = f"📊 *{result['symbol']}* – {result['direction']} CONFIRMÉ\n\n"
    caption += f"{result['comment']}\n\n"
    caption += f"🎯 Entrée : `{result['entry']}`\n"
    caption += f"🛡 SL : `{result['sl']}` | 🎯 TP : `{result['tp']}`\n"
    caption += f"#Swing #Crypto #Signal"

    with open(result["chart_path"], "rb") as img:
        bot.send_photo(chat_id=TELEGRAM_CHAT_ID, photo=img, caption=caption, parse_mode="Markdown")

def scan_and_send_signals():
    print("🔄 Scan démarré")
    symbols = get_all_perp_symbols()
    macro = load_macro_data()
    sent = load_sent_signals()

    for symbol in symbols:
        try:
            df = get_klines(symbol, interval="1h", limit=200)
            df_4h = get_klines(symbol, interval="4h", limit=200)

            if df is None or df.empty:
                continue

            df.name = symbol  # Pour chart

            for direction in ["long", "short"]:
                if already_sent(symbol, direction):
                    continue

                result = analyze_signal(
                    df=df,
                    symbol=symbol,
                    direction=direction,
                    df_4h=df_4h,
                    btc_df=macro["BTC"],
                    total_df=macro["TOTAL"],
                    btcd_df=macro["BTC.D"]
                )

                log_message(symbol, direction, result)

                if result["is_valid"]:
                    send_signal_to_telegram(result)
                    save_sent_signal(symbol, direction)
                    save_signal_to_csv(result)
        except Exception as e:
            print(f"⚠️ Erreur sur {symbol} : {e}")
