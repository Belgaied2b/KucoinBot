# main.py

import logging
import threading
from flask import Flask
from telegram.ext import Application, CommandHandler
from apscheduler.schedulers.background import BackgroundScheduler
from scanner import scan_and_send_signals, run_test_scan
from config import TOKEN, CHAT_ID

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

# Application Telegram
application = Application.builder().token(TOKEN).build()

# Handler pour /scan_test
async def scan_test_command(update, context):
    await update.message.reply_text("✅ Commande /scan_test reçue\n🚀 Début du scan test")
    await run_test_scan(context.bot)

application.add_handler(CommandHandler("scan_test", scan_test_command))

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

# Lancer le bot Telegram
def run_bot():
    print("🚀 Bot démarré avec scan automatique toutes les 10 minutes")
    application.run_polling()

# Démarrage : Flask + bot Telegram en parallèle
if __name__ == '__main__':
    threading.Thread(target=run_flask).start()
    run_bot()
