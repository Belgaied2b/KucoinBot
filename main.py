# main.py

import logging
import os
import threading
from flask import Flask
from telegram import Update
from telegram.ext import Application, CommandHandler, ContextTypes
from scanner import scan_and_send_signals  # on retire run_test_scan

# Configuration
TOKEN   = os.environ["TOKEN"]
CHAT_ID = os.environ["CHAT_ID"]

# Logger
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Flask app pour keep-alive
app = Flask(__name__)

@app.route("/")
def home():
    return "Bot is running!"

# /scan_test : déclenche un scan et envoie les signaux (graphes + alertes)
async def scan_test_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    logger.info("✅ Commande /scan_test reçue")
    await update.message.reply_text("Scan en cours…")
    await scan_and_send_signals(context.bot)

# /scan_graph : envoi aussi les signaux anticipés
async def scan_graph_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    logger.info("✅ Commande /scan_graph reçue")
    await update.message.reply_text("🚀 Envoi des signaux anticipés…")
    await scan_and_send_signals(context.bot)

# Job automatique toutes les 10 minutes
async def scheduled_scan(context: ContextTypes.DEFAULT_TYPE):
    logger.info("⏰ JobQueue déclenché — scan automatique")
    await scan_and_send_signals(context.bot)

def main():
    application = Application.builder().token(TOKEN).build()

    # Enregistrement des commandes
    application.add_handler(CommandHandler("scan_test", scan_test_command))
    application.add_handler(CommandHandler("scan_graph", scan_graph_command))

    # Planification auto
    application.job_queue.run_repeating(
        scheduled_scan,
        interval=600,  # toutes les 10 min
        first=1        # 1 s après démarrage
    )
    logger.info("🔁 Planification automatique configurée")

    # Flask en arrière-plan (keep-alive)
    threading.Thread(
        target=lambda: app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 3000))),
        daemon=True
    ).start()

    logger.info("🚀 Bot démarré — scan auto toutes les 10 min")
    application.run_polling()

if __name__ == "__main__":
    main()
