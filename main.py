# main.py

import logging
import asyncio
import threading
from flask import Flask
from telegram.ext import Application, CommandHandler
from apscheduler.schedulers.background import BackgroundScheduler
from scanner import scan_and_send_signals, run_test_scan
import os

# Logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Flask app (pour Railway)
app = Flask(__name__)

@app.route('/')
def home():
    return "Bot is running!"

def run_flask():
    app.run(host='0.0.0.0', port=3000)

# Token et ID depuis les variables Railway
TOKEN = os.getenv("TOKEN")
CHAT_ID = os.getenv("CHAT_ID")

# Application Telegram
application = Application.builder().token(TOKEN).build()

# Commande test
async def scan_test_command(update, context):
    await update.message.reply_text("✅ Commande /scan_test reçue\n🚀 Début du scan test")
    results = await run_test_scan(context.bot)
    if not results:
        await update.message.reply_text("📉 Aucun signal détecté.")
    else:
        for res in results:
            await context.bot.send_photo(chat_id=CHAT_ID, photo=open(res, "rb"))

application.add_handler(CommandHandler("scan_test", scan_test_command))

# Tâche automatique toutes les 10 minutes
scheduler = BackgroundScheduler()
scheduler.add_job(scan_and_send_signals, trigger='interval', minutes=10, args=[application.bot])
scheduler.start()

# Démarrer le bot Telegram
async def run_bot():
    await application.run_polling()

# Lancer Flask + bot
if __name__ == '__main__':
    logger.info("🚀 Bot démarré avec scan automatique toutes les 10 minutes")
    threading.Thread(target=run_flask).start()
    asyncio.run(run_bot())
