# main.py

import logging
import threading
from flask import Flask
from telegram.ext import Application, CommandHandler
from apscheduler.schedulers.background import BackgroundScheduler
from scanner import scan_and_send_signals, run_test_scan
from config import TOKEN

# Logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Flask app pour keep-alive
app = Flask(__name__)

@app.route('/')
def home():
    return "Bot is running!"

def run_flask():
    app.run(host='0.0.0.0', port=3000)

# Lancer le bot Telegram (sans asyncio.run)
def run_bot():
    application = Application.builder().token(TOKEN).build()

    # Ajout de la commande /scan_test
    application.add_handler(CommandHandler("scan_test", scan_test_command))

    # Planification du scan auto
    scheduler = BackgroundScheduler()
    scheduler.add_job(
        scan_and_send_signals,
        trigger='interval',
        minutes=10,
        args=[application.bot],
        max_instances=1,
        coalesce=True
    )
    scheduler.start()

    logger.info("🚀 Bot démarré avec scan automatique toutes les 10 minutes")
    application.run_polling()

# Fonction liée à la commande /scan_test
async def scan_test_command(update, context):
    await update.message.reply_text("✅ Commande /scan_test reçue\n\n🚀 Début du scan test")
    results = await run_test_scan(context.bot)
    if results:
        await update.message.reply_text(f"🧠 Résultats : {results}")
    else:
        await update.message.reply_text("✅ Scan terminé\n\n🧠 Aucun signal détecté.")

# Démarrage : Flask + bot Telegram
if __name__ == '__main__':
    threading.Thread(target=run_flask).start()
    run_bot()
