import logging
import os
import threading
from flask import Flask
from telegram import Update
from telegram.ext import Application, CommandHandler, ContextTypes
from scanner import scan_and_send_signals, run_test_scan

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

# /scan_test : scan de test (logs uniquement)
async def scan_test_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    logger.info("✅ Commande /scan_test reçue")
    await update.message.reply_text(
        "Scan en cours... (résultats uniquement visibles dans Railway)"
    )
    await run_test_scan(context.bot)

# /scan_graph : envoie les graphiques pour tous les signaux détectés
async def scan_graph_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    logger.info("✅ Commande /scan_graph reçue")
    await update.message.reply_text("🚀 Envoi des graphiques détectés…")
    await scan_and_send_signals(context.bot)

# Job automatique toutes les 10 min qui envoie les graphiques
async def scheduled_scan(context: ContextTypes.DEFAULT_TYPE):
    logger.info("⏰ JobQueue déclenché — scan automatique")
    await scan_and_send_signals(context.bot)

def main():
    application = Application.builder().token(TOKEN).build()

    # Handlers Telegram
    application.add_handler(CommandHandler("scan_test",  scan_test_command))
    application.add_handler(CommandHandler("scan_graph", scan_graph_command))

    # Planification auto : toutes les 600 s (10 min), premier run immédiat
    application.job_queue.run_repeating(
        scheduled_scan,
        interval=600,
        first=0
    )

    # Lancement de Flask en arrière-plan pour keep-alive
    threading.Thread(
        target=lambda: app.run(host="0.0.0.0", port=3000),
        daemon=True
    ).start()

    logger.info("🚀 Bot démarré — scan auto toutes les 10 min, /scan_graph pour les graphiques")
    application.run_polling()

if __name__ == "__main__":
    main()
