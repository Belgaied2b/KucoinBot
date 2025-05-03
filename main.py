import logging
import os
import threading
from flask import Flask
from telegram import Update
from telegram.ext import (
    Application,
    CommandHandler,
    ContextTypes
)
from scanner import scan_and_send_signals, run_test_scan

# Config
TOKEN   = os.environ["TOKEN"]
CHAT_ID = os.environ["CHAT_ID"]

# Logger
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Keep-alive web server
app = Flask(__name__)
@app.route("/")
def home():
    return "Bot is running!"

# Commande /scan_test (logs only)
async def scan_test_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    logger.info("✅ Commande /scan_test reçue")
    await run_test_scan(context.bot)

# Wrapper pour scheduler
async def scheduled_scan(context: ContextTypes.DEFAULT_TYPE):
    logger.info("🚀 Scan automatique déclenché par JobQueue")
    await scan_and_send_signals(context.bot)

def main():
    application = Application.builder().token(TOKEN).build()

    # Handler manuel
    application.add_handler(CommandHandler("scan_test", scan_test_command))

    # JobQueue toutes les 10 minutes (600 s)
    # first=0 pour lancer tout de suite au démarrage
    application.job_queue.run_repeating(
        scheduled_scan,
        interval=600,
        first=0
    )

    # Démarrage keep-alive Flask dans un thread
    threading.Thread(
        target=lambda: app.run(host="0.0.0.0", port=3000),
        daemon=True
    ).start()

    # Lance le bot
    logger.info("🚀 Bot démarré, écoute Telegram + scan auto toutes les 10 min")
    application.run_polling()

if __name__ == "__main__":
    main()
