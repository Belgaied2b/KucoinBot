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

# Flask app pour keep-alive (Railway)
app = Flask(__name__)

@app.route('/')
def home():
    return "Bot is running!"

def run_flask():
    app.run(host='0.0.0.0', port=3000)

# Bot Telegram
application = Application.builder().token(TOKEN).build()

# Scan toutes les 10 minutes
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
