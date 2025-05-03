import logging
import os
import threading
from flask import Flask
from telegram import Update
from telegram.ext import Application, CommandHandler, ContextTypes
from apscheduler.schedulers.background import BackgroundScheduler
from scanner import scan_and_send_signals, run_test_scan

# Configuration
TOKEN = os.environ["TOKEN"]
CHAT_ID = os.environ["CHAT_ID"]

# Logger
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Flask keep-alive
app = Flask(__name__)

@app.route("/")
def home():
    return "Bot is running!"

# Commande Telegram
async def scan_test_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    logger.info("✅ Commande /scan_test reçue")
    await update.message.reply_text("🚀 Début du scan test")
    results = await run_test_scan(context.bot)
    if results:
        for msg in results:
            await update.message.reply_text(msg)
    else:
        await update.message.reply_text("""✅ Scan terminé\n\n🧠 Aucun signal détecté.""")

# Lancer Flask dans un thread
def run_flask():
    app.run(host="0.0.0.0", port=3000)

# Lancer le bot Telegram dans le thread principal
def run_bot():
    application = Application.builder().token(TOKEN).build()
    application.add_handler(CommandHandler("scan_test", scan_test_command))

    # Scheduler
    scheduler = BackgroundScheduler()
    scheduler.add_job(scan_and_send_signals, "interval", minutes=10, args=[application.bot])
    scheduler.start()
    logger.info("🚀 Bot démarré avec scan automatique toutes les 10 minutes")

    application.run_polling()

if __name__ == "__main__":
    threading.Thread(target=run_flask).start()
    run_bot()
