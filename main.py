# main.py

import logging
import os
import threading

from flask import Flask
from telegram import Update
from telegram.ext import Application, CommandHandler, ContextTypes

from scanner import scan_and_send_signals
from analyse_stats import compute_stats

# Configuration
TOKEN   = os.environ["TOKEN"]
CHAT_ID = os.environ["CHAT_ID"]

# Logger
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Flask app for keep-alive
app = Flask(__name__)

@app.route("/")
def home():
    return "Bot is running!"

# --- Stats logging job ---
async def log_stats(context: ContextTypes.DEFAULT_TYPE):
    # Silence les logs verbeux
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("kucoin_utils").setLevel(logging.WARNING)

    df_stats, df_means = compute_stats(limit=50)

    logger.warning(
        "🔢 Stats détaillées par symbole:\n%s",
        df_stats.to_string(index=False, max_rows=20)
    )
    logger.warning(
        "📊 Moyennes (Signal vs No-Signal):\n%s",
        df_means.to_string(index=False)
    )

# /scan_test : déclenche un scan et envoie les signaux (graphes + alertes)
async def scan_test_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    logger.info("✅ Commande /scan_test reçue")
    await update.message.reply_text("Scan en cours…")
    await scan_and_send_signals(context.bot)

# /scan_graph : envoi des signaux anticipés
async def scan_graph_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    logger.info("✅ Commande /scan_graph reçue")
    await update.message.reply_text("🚀 Envoi des signaux anticipés…")
    await scan_and_send_signals(context.bot)

# Scan automatique toutes les 10 minutes
async def scheduled_scan(context: ContextTypes.DEFAULT_TYPE):
    logger.info("⏰ JobQueue déclenché — scan automatique")
    await scan_and_send_signals(context.bot)

def main():
    application = Application.builder().token(TOKEN).build()

    # Handlers Telegram
    application.add_handler(CommandHandler("scan_test", scan_test_command))
    application.add_handler(CommandHandler("scan_graph", scan_graph_command))

    # Planification jobs
    # 1) Stats une seule fois 5s après démarrage
    application.job_queue.run_once(log_stats, when=5)
    # 2) Scan automatique répété toutes les 600s (10 min), premier run immédiat
    application.job_queue.run_repeating(scheduled_scan, interval=600, first=1)

    # Keep-alive Flask en arrière-plan
    threading.Thread(
        target=lambda: app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 3000))),
        daemon=True
    ).start()

    logger.info("🚀 Bot démarré — scan auto toutes les 10 min, stats loggées après 5s")
    application.run_polling()

if __name__ == "__main__":
    main()
