# main.py

import logging
import threading
from flask import Flask
from telegram.ext import Application
from apscheduler.schedulers.background import BackgroundScheduler
from scanner import scan_and_send_signals
from config import TOKEN, CHAT_ID

# Configuration du logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Serveur Flask pour garder le bot en vie
app = Flask(__name__)

@app.route('/')
def home():
    return "Bot is running!"

# Fonction pour démarrer le serveur Flask
def run_flask():
    app.run(host='0.0.0.0', port=3000)

# Initialisation de l'application Telegram
application = Application.builder().token(TOKEN).build()

# Planificateur pour exécuter scan_and_send_signals automatiquement
scheduler = BackgroundScheduler()
scheduler.add_job(scan_and_send_signals, 'interval', hours=1, args=[application.bot])
scheduler.start()

# Fonction pour démarrer le bot Telegram
def run_bot():
    application.run_polling()

# Lancement combiné Flask + Bot dans deux threads séparés
if __name__ == '__main__':
    threading.Thread(target=run_flask).start()
    run_bot()
