# main.py

import logging
import threading
import asyncio
from flask import Flask
from telegram import Update
from telegram.ext import Application, CommandHandler, ContextTypes
from apscheduler.schedulers.background import BackgroundScheduler
from scanner import scan_and_send_signals, run_test_scan
import os

# Logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Lire les variables d'environnement (depuis Railway)
TOKEN = os.getenv("TOKEN")
CHAT_ID = os.getenv("CHAT_ID")

# Flask app pour keep-alive
app = Flask(__name__)

@app.route('/')
def home():
    return "Bot is running!"

def run_flask():
    app.run(host='0.0.0.0', port=3000)

# Commande Telegram : /scan_test
async def scan_test_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await context.bot.send_message(chat_id=update.effective_chat.id, text="✅ Commande /scan_test reçue")
    await run_test_scan(context.bot)

# Création de l’application Telegram
application = Application.builder().token(TOKEN).build()

# Ajout du handler pour /scan_test
application.add_handler(CommandHandler("scan_test", scan_test_command))

# Planification du scan automatique toutes les 10 minutes
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

# Démarrer le bot
def run_bot():
    print("🚀 Bot démarré avec scan automatique toutes les 10 minutes")
    asyncio.run(application.run_polling())

# Lancer Flask + le bot Telegram
if __name__ == '__main__':
    threading.Thread(target=run_flask).start()
    run_bot()
