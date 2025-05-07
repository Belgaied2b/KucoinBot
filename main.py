import os
import logging
from telegram.ext import Application, CommandHandler
from apscheduler.schedulers.background import BackgroundScheduler
from scanner import scan_and_send_signals

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

BOT_TOKEN = os.getenv("TOKEN")
CHAT_ID = int(os.getenv("CHAT_ID"))

async def start(update, context):
    await update.message.reply_text("✅ Bot actif. Utilise /scan pour lancer une analyse manuelle.")

async def scan(update, context):
    await scan_and_send_signals(context.bot, CHAT_ID)

def main():
    application = Application.builder().token(BOT_TOKEN).build()

    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("scan", scan))

    # ✔️ Planification du scan via application.create_task
    scheduler = BackgroundScheduler(timezone="UTC")

    def job():
        application.create_task(scan_and_send_signals(application.bot, CHAT_ID))

    scheduler.add_job(job, 'interval', minutes=5)
    scheduler.start()

    logger.info("🚀 Bot lancé avec scan automatique toutes les 5 minutes.")
    application.run_polling()

if __name__ == "__main__":
    main()
