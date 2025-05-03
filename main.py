# main.py

import logging
import threading
from flask import Flask
from telegram.ext import Application
from apscheduler.schedulers.background import BackgroundScheduler
from scanner import scan_and_send_signals
from config import TOKEN

# Logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Flask server
app = Flask(__name__)

@app.route('/')
def home():
    return "Bot is running!"

def run_flask():
    app.run(host='0.0.0.0', port=3000)

# Telegram application
application = Application.builder().token(TOKEN).build()

# Planification du scan toutes les 10 minutes
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

# 🚀 Lancement principal
if __name__ == '__main__':
    print("🚀 Bot démarré avec scan automatique toutes les 10 minutes", flush=True)
    threading.Thread(target=run_flask).start()  # Lancer Flask dans un thread
    application.run_polling()  # Garder run_polling dans le thread principal
