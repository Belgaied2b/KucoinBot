import logging, os, threading
from flask import Flask
from telegram import Update
from telegram.ext import Application, CommandHandler, ContextTypes
from scanner import scan_and_send_signals, run_test_scan

# Config
TOKEN   = os.environ["TOKEN"]
CHAT_ID = os.environ["CHAT_ID"]

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Keep-alive
app = Flask(__name__)
@app.route("/")
def home(): return "Bot is running!"

# scan_test : log only
async def scan_test_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    logger.info("✅ Commande /scan_test reçue")
    await update.message.reply_text("Scan en cours… (logs seulement)")
    await run_test_scan(context.bot)

# scan_graph : envoie les graphiques
async def scan_graph_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    logger.info("✅ Commande /scan_graph reçue")
    await update.message.reply_text("🚀 Envoi des graphiques…")
    await scan_and_send_signals(context.bot)

# Job auto
async def scheduled_scan(context: ContextTypes.DEFAULT_TYPE):
    logger.info("⏰ JobQueue déclenché — scan auto")
    await scan_and_send_signals(context.bot)

def main():
    application = Application.builder().token(TOKEN).build()

    # Handlers
    application.add_handler(CommandHandler("scan_test",  scan_test_command))
    application.add_handler(CommandHandler("scan_graph", scan_graph_command))

    # Planif auto (10 min), 1 s après le start
    application.job_queue.run_repeating(
        scheduled_scan,
        interval=600,
        first=1
    )
    logger.info("🔁 Planification automatique (JobQueue) configurée")

    # Keep-alive Flask en arrière-plan
    threading.Thread(
        target=lambda: app.run(host="0.0.0.0", port=3000),
        daemon=True
    ).start()

    logger.info("🚀 Bot démarré, écoute Telegram + scan auto toutes les 10 min")
    application.run_polling()

if __name__ == "__main__":
    main()
