import asyncio
from telegram import Bot
from telegram.ext import Application, CommandHandler
from signal_detector import auto_scan_and_send_signals
from keep_alive import keep_alive
import logging

# Configuration
from config import TELEGRAM_TOKEN, TELEGRAM_CHAT_ID

# Logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Commandes manuelles
async def scan_command(update, context):
    await update.message.reply_text("🔍 Scan manuel lancé...")
    await auto_scan_and_send_signals()

# Lancement du bot
async def main():
    keep_alive()

    application = Application.builder().token(TELEGRAM_TOKEN).build()
    application.add_handler(CommandHandler("scan", scan_command))

    async def periodic_scan():
        while True:
            await auto_scan_and_send_signals()
            await asyncio.sleep(600)  # 10 minutes

    # Tâche de scan automatique
    application.create_task(periodic_scan())
    await application.run_polling()

if __name__ == '__main__':
    asyncio.run(main())
