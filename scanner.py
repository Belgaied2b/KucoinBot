import os
import json
import logging
import asyncio
import telegram
from kucoin_utils import get_perp_symbols, get_klines
from signal_analysis import analyze_signal

# ✅ Configuration des logs pour Railway
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s"
)

# 📡 Telegram
BOT_TOKEN = os.getenv("TOKEN")
CHAT_ID = os.getenv("CHAT_ID")
bot = telegram.Bot(token=BOT_TOKEN)

# 🧠 Mémoire des signaux envoyés
SENT_SIGNALS_FILE = "sent_signals.json"
if not os.path.exists(SENT_SIGNALS_FILE):
    with open(SENT_SIGNALS_FILE, "w") as f:
        json.dump([], f)

def load_sent_signals():
    with open(SENT_SIGNALS_FILE, "r") as f:
        return json.load(f)

def save_sent_signal(symbol, direction):
    sent_signals = load_sent_signals()
    sent_signals.append(f"{symbol}_{direction}")
    with open(SENT_SIGNALS_FILE, "w") as f:
        json.dump(sent_signals, f)

def already_sent(symbol, direction):
    return f"{symbol}_{direction}" in load_sent_signals()

# 🚀 Fonction principale
async def scan_and_send_signals():
    logging.info("🔍 Scan en cours...")
    symbols = get_perp_symbols()

    for symbol in symbols:
        df_1h = get_klines(symbol, interval="1hour", limit=200)
        df_4h = get_klines(symbol, interval="4hour", limit=100)

        if df_1h is None or df_1h.empty or df_4h is None or df_4h.empty:
            logging.warning(f"⚠️ Données manquantes pour {symbol}, skip.")
            continue

        df_1h.name = symbol  # Pour logs et graphiques

        for direction in ["long", "short"]:
            result = analyze_signal(df_1h, df_4h, direction)

            if not result or not result.get("valide"):
                logging.info(f"⛔ {symbol} ({direction}) rejeté ou non valide.")
                continue

            if already_sent(symbol, direction):
                logging.info(f"⏭️ Signal déjà envoyé pour {symbol} ({direction})")
                continue

            message = (
                f"💥 Signal {direction.upper()} détecté sur {symbol}\n\n"
                f"{result['commentaire']}\n\n"
                f"🎯 Entrée : {result['entry']:.4f}\n"
                f"⛔ SL : {result['sl']:.4f}\n"
                f"✅ TP : {result['tp']:.4f}\n"
                f"📊 Score qualité : {result['score']}/10"
            )

            try:
                await bot.send_message(chat_id=CHAT_ID, text=message)
                logging.info(f"📩 Signal envoyé pour {symbol} ({direction})")
                save_sent_signal(symbol, direction)
            except Exception as e:
                logging.error(f"❌ Erreur envoi Telegram {symbol} : {e}")
