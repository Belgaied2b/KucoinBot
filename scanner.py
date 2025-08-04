import time
import requests
import pandas as pd
from kucoin_utils import get_klines, get_all_symbols
from signal_analysis import analyze_signal
from telegram import Bot
import os

bot = Bot(token=os.getenv("TOKEN"))
chat_id = os.getenv("CHAT_ID")


def send_telegram_message(message):
    try:
        bot.send_message(chat_id=chat_id, text=message)
    except Exception as e:
        print(f"Erreur envoi Telegram : {e}")


def scan_and_send_signals():
    print("🔁 Scan démarré...\n")

    all_symbols = get_all_symbols()
    if not all_symbols:
        print("❌ Impossible de récupérer les symboles.")
        return

    for symbol in all_symbols:
        try:
            df = get_klines(symbol, interval='1hour', limit=150)
            if df is None or df.empty:
                print(f"⛔ Données manquantes pour {symbol}")
                continue

            df.name = symbol  # Pour analyse
            df_4h = get_klines(symbol, interval='4hour', limit=100)

            for direction in ['long', 'short']:
                result = analyze_signal(df, df_4h, direction)

                if result is None:
                    print(f"[{symbol.upper()} - {direction.upper()}] ⛔ Analyse impossible (Données incomplètes)\n")
                    continue

                # ✅ Log clair dans tous les cas
                score = result.get("score", 0)
                rejetes = ", ".join(result.get("rejetes", [])) or "Aucun"
                toleres = ", ".join(result.get("toleres", [])) or "Aucun"

                if result["valide"]:
                    print(f"[{symbol.upper()} - {direction.upper()}] ✅ VALIDE | Score: {score}/10 | ❌ {rejetes} | ⚠️ {toleres}\n")
                    send_telegram_message(result["commentaire"])
                else:
                    print(f"[{symbol.upper()} - {direction.upper()}] ❌ REJETÉ | Score: {score}/10 | ❌ {rejetes} | ⚠️ {toleres}\n")

        except Exception as e:
            print(f"⛔ Erreur avec {symbol}: {e}")

    print("✅ Scan terminé.\n")
