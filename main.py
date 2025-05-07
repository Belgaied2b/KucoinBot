import os
import logging
import asyncio
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

def job_scan():
    asyncio.run(scan_and_send_signals(app.bot, CHAT_ID))

def main():
    global app
    app = Application.builder().token(BOT_TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("scan", scan))

    # 🚀 Scheduler stable avec asyncio.run()
    scheduler = BackgroundScheduler(timezone="UTC")
    scheduler.add_job(job_scan, 'interval', minutes=5)
    scheduler.start()

    logger.info("🚀 Bot lancé avec scan auto toutes les 5 min.")

    # 🔥 Scan immédiat au lancement
    app.create_task(scan_and_send_signals(app.bot, CHAT_ID))

    app.run_polling()

if __name__ == "__main__":
    main()
