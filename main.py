import os
import logging
import asyncio
import threading
from telegram.ext import Application, CommandHandler
from apscheduler.schedulers.background import BackgroundScheduler
from scanner import scan_and_send_signals
from signal_updater import check_active_signals_and_update

# 🧹 Suppression automatique des signaux enregistrés (reset à chaque redémarrage)
if os.path.exists("sent_signals.json"):
    os.remove("sent_signals.json")
    print("[🧹] Fichier sent_signals.json supprimé au démarrage. Réinitialisation des signaux.")

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

BOT_TOKEN = os.getenv("TOKEN")
CHAT_ID = int(os.getenv("CHAT_ID"))

# ▶️ Commande /start
async def start(update, context):
    await update.message.reply_text("✅ Bot actif. Utilise /scan pour lancer une analyse manuelle.")

# ▶️ Commande /scan
async def scan(update, context):
    await scan_and_send_signals(context.bot, CHAT_ID)

# 🔁 Scan immédiat au démarrage
async def post_init(application):
    logger.info("🔥 Scan immédiat au démarrage")
    await scan_and_send_signals(application.bot, CHAT_ID)

# 🔁 Scan automatique toutes les 10 minutes
def job_scan():
    async def wrapper():
        await scan_and_send_signals(app.bot, CHAT_ID)
    def runner():
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        loop.run_until_complete(wrapper())
    threading.Thread(target=runner).start()

# 🔁 Mise à jour automatique des signaux existants
def job_update_signals():
    async def wrapper():
        await check_active_signals_and_update(app.bot, CHAT_ID)
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

    scheduler = BackgroundScheduler(timezone="UTC")
    scheduler.add_job(job_scan, 'interval', minutes=10)
    scheduler.add_job(job_update_signals, 'interval', minutes=10)
    scheduler.start()

    logger.info("🚀 Bot lancé avec scan auto + mise à jour des signaux toutes les 10 minutes")
    app.run_polling()

if __name__ == "__main__":
    main()
