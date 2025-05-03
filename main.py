import logging
import threading
from flask import Flask
from telegram.ext import Application, CommandHandler
from apscheduler.schedulers.background import BackgroundScheduler
from scanner import scan_and_send_signals, run_test_scan
import os

# Logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Flask pour keep-alive
app = Flask(__name__)

@app.route('/')
def home():
    return "Bot is running!"

def run_flask():
    app.run(host='0.0.0.0', port=3000)

# Bot Telegram
TOKEN = os.getenv("TOKEN")
application = Application.builder().token(TOKEN).build()

# Commande /scan_test
application.add_handler(CommandHandler("scan_test", run_test_scan))

# Planification du scan auto toutes les 10 minutes
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
print("🚀 Bot démarré avec scan automatique toutes les 10 minutes")

# Démarrage parallèle
def run_bot():
    application.run_polling()

if __name__ == '__main__':
    threading.Thread(target=run_flask).start()
    run_bot()
