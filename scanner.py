import os
import time
from telegram import Bot
from kucoin_utils import fetch_all_symbols, fetch_klines
from signal_analysis import analyze_signal

bot = Bot(token=os.getenv("TOKEN"))
chat_id = os.getenv("CHAT_ID")

def send_telegram_message(message):
    try:
        bot.send_message(chat_id=chat_id, text=message)
    except Exception as e:
        print(f"❌ Erreur envoi Telegram : {e}")

def scan_and_send_signals():
    print("🔁 Scan démarré...\n")

    all_symbols = fetch_all_symbols()
    if not all_symbols:
        print("❌ Impossible de récupérer les symboles.")
        return

    for symbol in all_symbols:
        try:
            df = fetch_klines(symbol, interval="1h", limit=150)
            df_4h = fetch_klines(symbol, interval="4h", limit=100)

            if df is None or df.empty or df_4h is None or df_4h.empty:
                print(f"[{symbol}] ⛔ Données manquantes")
                continue

            df.name = symbol

            for direction in ["long", "short"]:
                result = analyze_signal(df, df_4h, direction)

                if result is None:
                    print(f"[{symbol.upper()} - {direction.upper()}] ⛔ Analyse impossible\n")
                    continue

                score = result.get("score", 0)
                rejetes = ", ".join(result.get("rejetes", [])) or "Aucun"
                toleres = ", ".join(result.get("toleres", [])) or "Aucun"

                if result["valide"]:
                    print(f"[{symbol.upper()} - {direction.upper()}] ✅ VALIDE | Score: {score}/10 | ❌ {rejetes} | ⚠️ {toleres}\n")
                    send_telegram_message(result["commentaire"])
                else:
                    print(f"[{symbol.upper()} - {direction.upper()}] ❌ REJETÉ | Score: {score}/10 | ❌ {rejetes} | ⚠️ {toleres}\n")

        except Exception as e:
            print(f"[{symbol}] ⛔ Erreur analyse : {e}")

    print("✅ Scan terminé.\n")
