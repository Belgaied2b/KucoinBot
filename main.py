# main.py

import os
import logging
import threading
import asyncio
import datetime

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

    # Scheduler toutes les 10 minutes, et tout de suite au démarrage
    loop = asyncio.get_event_loop()
    scheduler = BackgroundScheduler()

    # job principal
    job = scheduler.add_job(
        lambda: asyncio.run_coroutine_threadsafe(
            scan_and_send_signals(application.bot), loop
        ),
        trigger="interval",
        minutes=10,
        next_run_time=datetime.datetime.now(),  # exécute immédiatement
        id="crypto_scan"
    )
    scheduler.start()
    logger.info("Scheduler démarré — scan toutes les 10 minutes (1er run immédiat)")

    # Serveur web en arrière-plan
    threading.Thread(
        target=lambda: app.run(host="0.0.0.0", port=int(os.environ.get("PORT",3000))),
        daemon=True
    ).start()

    logger.info("🚀 Bot démarré — lancement du scan et en attente de /stats")
    application.run_polling()

if __name__ == "__main__":
    main()
