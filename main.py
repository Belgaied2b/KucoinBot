import os
import logging
import asyncio
import threading
from telegram.ext import Application, CommandHandler
from apscheduler.schedulers.background import BackgroundScheduler
from scanner import scan_and_send_signals

# 📋 Configuration logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# 🔐 Variables d’environnement (Railway)
BOT_TOKEN = os.getenv("TOKEN")
CHAT_ID = int(os.getenv("CHAT_ID"))

# 📍 Commande /start
async def start(update, context):
    if str(update.effective_chat.id) != str(CHAT_ID):
        await update.message.reply_text("⛔️ Accès refusé.")
        return
    await update.message.reply_text("✅ Bot actif. Utilise /scan pour lancer une analyse manuelle.")

# 📍 Commande /scan
async def scan(update, context):
    if str(update.effective_chat.id) != str(CHAT_ID):
        await update.message.reply_text("⛔️ Accès refusé.")
        return
    await update.message.reply_text("🔍 Scan manuel en cours...")
    await scan_and_send_signals()
    await update.message.reply_text("✅ Scan terminé.")

# 🔁 Scan immédiat au démarrage
async def post_init(application):
    logger.info("🔥 Scan immédiat au démarrage")
    await scan_and_send_signals()

# 🔁 Scan automatique toutes les 5 minutes (thread + event loop)
def job_scan():
    async def wrapper():
        await scan_and_send_signals()

    def runner():
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        loop.run_until_complete(wrapper())

    threading.Thread(target=runner).start()

# 🚀 Lancement du bot
def main():
    global app
    app = Application.builder().token(BOT_TOKEN).post_init(post_init).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("scan", scan))

    # 🔁 Scheduler toutes les 5 minutes
    scheduler = BackgroundScheduler(timezone="UTC")
    scheduler.add_job(job_scan, 'interval', minutes=5)
    scheduler.start()

    logger.info("🚀 Bot lancé avec scan auto toutes les 5 minutes + commande /scan")
    app.run_polling()

if __name__ == "__main__":
    main()
