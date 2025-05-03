import os
import logging
import threading
from flask import Flask
from telegram.ext import Application, CommandHandler
from apscheduler.schedulers.background import BackgroundScheduler
from scanner import scan_and_send_signals, run_test_scan

# Logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Token & Chat ID depuis Railway
TOKEN = os.environ.get("TOKEN")
CHAT_ID = os.environ.get("CHAT_ID")

# Flask app (pour Railway)
app = Flask(__name__)

@app.route('/')
def home():
    return "Bot is running!"

def run_flask():
    app.run(host='0.0.0.0', port=3000)

# Commande /scan_test
async def scan_test_command(update, context):
    await update.message.reply_text("✅ Commande /scan_test reçue\n\n🚀 Début du scan test")
    await run_test_scan(context.bot)

# App Telegram
application = Application.builder().token(TOKEN).build()
application.add_handler(CommandHandler("scan_test", scan_test_command))

# Planificateur
scheduler = BackgroundScheduler()
scheduler.add_job(scan_and_send_signals, 'interval', minutes=10, args=[application.bot])
scheduler.start()

# Lancement
def run_bot():
    logger.info("🚀 Bot démarré avec scan automatique toutes les 10 minutes")
    application.run_polling()

if __name__ == '__main__':
    threading.Thread(target=run_flask).start()
    run_bot()
