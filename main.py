import asyncio
import os
import logging
from telegram import Bot
from telegram.ext import Application, CommandHandler
from signal_detector import auto_scan_and_send_signals

# Variables d'environnement
TOKEN = os.getenv("TOKEN")
CHAT_ID = os.getenv("CHAT_ID")
BOT = Bot(token=TOKEN)

# Logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Commande manuelle
async def scan_command(update, context):
    await update.message.reply_text("🔍 Scan manuel lancé...")
    await auto_scan_and_send_signals(BOT, CHAT_ID)

# Lancement du bot
async def main():
    application = Application.builder().token(TOKEN).build()
    application.add_handler(CommandHandler("scan", scan_command))

    async def periodic_scan():
        while True:
            await auto_scan_and_send_signals(BOT, CHAT_ID)
            await asyncio.sleep(600)  # toutes les 10 min

    application.create_task(periodic_scan())
    await application.run_polling()

if __name__ == '__main__':
    asyncio.run(main())
