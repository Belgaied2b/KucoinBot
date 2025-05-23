import os
import logging
import asyncio
import threading
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
    await scan_and_send_signals()

# 🔁 Scan immédiat au démarrage
async def post_init(application):
    logger.info("🔥 Scan immédiat au démarrage")
    await scan_and_send_signals()

# ✅ Scan automatique toutes les 10 minutes dans un thread
def job_scan():
    async def wrapper():
        await scan_and_send_signals()

    def runner():
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        loop.run_until_complete(wrapper())

    threading.Thread(target=runner).start()

def main():
    global app
    app = Application.builder().token(BOT_TOKEN).post_init(post_init).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("scan", scan))

    scheduler = BackgroundScheduler(timezone="UTC")
    scheduler.add_job(job_scan, 'interval', minutes=10)
    scheduler.start()

    logger.info("🚀 Bot lancé avec scan auto toutes les 10 minutes + scan immédiat")
    app.run_polling()

if __name__ == "__main__":
    main()
