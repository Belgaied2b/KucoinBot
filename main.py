# main.py

import logging
import threading
from flask import Flask
from telegram.ext import Application, CommandHandler
from apscheduler.schedulers.background import BackgroundScheduler
from scanner import scan_and_send_signals
from analyse_test import run_test_analysis
from config import TOKEN

# Logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = Flask(__name__)

@app.route('/')
def home():
    return "Bot is running!"

def run_flask():
    app.run(host='0.0.0.0', port=3000)

# Commande /scan_test
async def scan_test_command(update, context):
    print("✅ Commande /scan_test reçue")
    results = await run_test_analysis()
    print("🧠 Résultats:", results)
    if results:
        for symbol, side in results:
            await context.bot.send_message(chat_id=update.effective_chat.id, text=f"{side} 🚀 {symbol} | 4H (TEST)")
    else:
        await context.bot.send_message(chat_id=update.effective_chat.id, text="Aucun signal détecté.")

application = Application.builder().token(TOKEN).build()
application.add_handler(CommandHandler("scan_test", scan_test_command))

scheduler = BackgroundScheduler()
scheduler.add_job(
    scan_and_send_signals,
    trigger='interval',
    minutes=10,
    args=[application.bot],
    max_instances=1,
    coalesce=True
)
scheduler.start()

def run_bot():
    application.run_polling()

if __name__ == '__main__':
    threading.Thread(target=run_flask).start()
    run_bot()
