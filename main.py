# main.py

import os
import logging
import threading
import asyncio
from flask import Flask
from telegram.ext import Application
from apscheduler.schedulers.background import BackgroundScheduler
from scanner import scan_and_send_signals

# Logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Récupérer les variables Railway
TOKEN = os.environ.get("TOKEN")
CHAT_ID = os.environ.get("CHAT_ID")

# Flask app pour keep-alive
app = Flask(__name__)

@app.route('/')
def home():
    return "Bot is running!"

def run_flask():
    app.run(host='0.0.0.0', port=3000)

# Initialisation du bot Telegram
application = Application.builder().token(TOKEN).build()

# Planification du scan toutes les 10 minutes avec gestion async correcte
scheduler = BackgroundScheduler()
scheduler.add_job(
    lambda: asyncio.create_task(scan_and_send_signals(application.bot)),
    trigger='interval',
    minutes=10,
    max_instances=1,
    coalesce=True
)
scheduler.start()
print("🚀 Bot démarré avec scan automatique toutes les 10 minutes")

# Lancer le bot Telegram
def run_bot():
    asyncio.run(application.run_polling())

# Démarrage Flask + bot Telegram en parallèle
if __name__ == '__main__':
    threading.Thread(target=run_flask).start()
    run_bot()
