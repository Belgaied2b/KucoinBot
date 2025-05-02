# main.py

import logging
import threading
from flask import Flask
from telegram.ext import Application, CommandHandler
from apscheduler.schedulers.background import BackgroundScheduler
from scanner import scan_and_send_signals
from analyse_test import run_test_analysis  # ← le fichier que tu vas créer
from config import TOKEN

# Logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Flask app pour keep-alive
app = Flask(__name__)

@app.route('/')
def home():
    return "Bot is running!"

def run_flask():
    app.run(host='0.0.0.0', port=3000)

# Commande /scan_test Telegram
async def scan_test_command(update, context):
    results = await run_test_analysis()
    if results:
        for symbol, side in results:
            await context.bot.send_message(chat_id=update.effective_chat.id, text=f"{side} 🚀 {symbol} | 4H (TEST)")
    else:
        await context.bot.send_message(chat_id=update.effective_chat.id, text="Aucun signal détecté.")

# Application Telegram
application = Application.builder().token(TOKEN).build()

# Ajout de la commande manuelle
application.add_handler(CommandHandler("scan_test", scan_test_command))

# Scan auto toutes les 10 min (bot principal)
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

# Lancer le bot Telegram
def run_bot():
    application.run_polling()

# Lancement Flask + bot
if __name__ == '__main__':
    threading.Thread(target=run_flask).start()
    run_bot()
