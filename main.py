import logging
import os
import threading
from flask import Flask
from telegram.ext import ApplicationBuilder, CommandHandler
from scanner import scan_and_send_signals
from analyse_stats import compute_stats
from apscheduler.schedulers.background import BackgroundScheduler

# Configuration
TOKEN   = os.environ["TOKEN"]
CHAT_ID = os.environ["CHAT_ID"]

# Logging
logging.basicConfig(
    level    = logging.INFO,
    format   = "%(asctime)s %(levelname)s %(message)s",
    datefmt  = "%H:%M:%S"
)
logger = logging.getLogger(__name__)

def main():
    # Flask pour keep-alive (Heroku, etc.)
    app = Flask(__name__)

    # Bot Telegram
    application = ApplicationBuilder().token(TOKEN).build()

    # /stats command
    async def stats_handler(update, context):
        stats_text = compute_stats()
        await context.bot.send_message(
            chat_id=CHAT_ID,
            text=stats_text,
            parse_mode="Markdown"
        )
    application.add_handler(CommandHandler("stats", stats_handler))

    # Scheduler : on lance le scan toutes les 1 min
    scheduler = BackgroundScheduler()
    scheduler.add_job(
        lambda: application.create_task(scan_and_send_signals(application.bot)),
        "interval", minutes=1
    )
    scheduler.start()

    # Lancement du web-server en arrière-plan
    threading.Thread(
        target=lambda: app.run(
            host="0.0.0.0",
            port=int(os.environ.get("PORT", 3000))
        ),
        daemon=True
    ).start()

    logger.info("🚀 Bot démarré — scan+stats auto toutes les 1 min")
    application.run_polling()

if __name__ == "__main__":
    main()
