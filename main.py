# main.py

import os
import logging
import threading
import asyncio

from flask import Flask
from apscheduler.schedulers.background import BackgroundScheduler
from telegram.ext import ApplicationBuilder, CommandHandler

from scanner import scan_and_send_signals
from analyse_stats import compute_stats

# ─────────── Logging ───────────
logging.basicConfig(
    level    = logging.INFO,
    format   = "%(asctime)s %(levelname)s %(message)s",
    datefmt  = "%H:%M:%S"
)
logger = logging.getLogger(__name__)

TOKEN   = os.environ["TOKEN"]
CHAT_ID = os.environ["CHAT_ID"]

def main():
    # Flask pour keep-alive
    app = Flask(__name__)

    # Bot Telegram
    application = ApplicationBuilder().token(TOKEN).build()

    # Commande /stats
    async def stats_handler(update, context):
        text = compute_stats()
        await context.bot.send_message(chat_id=CHAT_ID, text=text, parse_mode="Markdown")
    application.add_handler(CommandHandler("stats", stats_handler))

    # Scheduler toutes les 10 minutes
    loop = asyncio.get_event_loop()
    scheduler = BackgroundScheduler()
    scheduler.add_job(
        lambda: asyncio.run_coroutine_threadsafe(
            scan_and_send_signals(application.bot), loop
        ),
        trigger="interval",
        minutes=10,
        id="crypto_scan"
    )
    scheduler.start()
    logger.info("Scheduler démarré — scan toutes les 10 minutes")

    # Serveur web
    threading.Thread(
        target=lambda: app.run(host="0.0.0.0", port=int(os.environ.get("PORT",3000))),
        daemon=True
    ).start()

    logger.info("🚀 Bot démarré — en attente de /stats et scans")
    application.run_polling()

if __name__ == "__main__":
    main()
