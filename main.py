import os
import logging
import asyncio
from functools import partial
from telegram.ext import Application, CommandHandler
from apscheduler.schedulers.background import BackgroundScheduler
from scanner import scan_and_send_signals

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

BOT_TOKEN = os.getenv("TOKEN")
CHAT_ID = int(os.getenv("CHAT_ID"))

# Commande manuelle /start
async def start(update, context):
    await update.message.reply_text("✅ Bot actif. Utilise /scan pour lancer une analyse manuelle.")

# Commande manuelle /scan
async def scan(update, context):
    await scan_and_send_signals(context.bot, CHAT_ID)

# Fonction utilisée par le scheduler (scan auto)
async def scan_job(bot):
    await scan_and_send_signals(bot, CHAT_ID)

def main():
    application = Application.builder().token(BOT_TOKEN).build()

    # Commandes Telegram
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("scan", scan))

    # Planification du scan auto toutes les 5 minutes
    scheduler = BackgroundScheduler(timezone="UTC")
    scheduler.add_job(lambda: asyncio.create_task(scan_job(application.bot)), 'interval', minutes=5)
    scheduler.start()

    logger.info("🚀 Bot lancé avec scan automatique toutes les 5 minutes.")
    application.run_polling()

if __name__ == "__main__":
    main()
