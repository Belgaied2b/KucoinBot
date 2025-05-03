# main.py

import logging
import threading
import asyncio
from flask import Flask
from telegram.ext import Application, CommandHandler
from apscheduler.schedulers.background import BackgroundScheduler
from scanner import scan_and_send_signals, run_test_scan
from config import TOKEN

# Logger
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Flask app pour le keep-alive
app = Flask(__name__)

@app.route('/')
def home():
    return "Bot is running!"

def run_flask():
    app.run(host='0.0.0.0', port=3000)

# Application Telegram
application = Application.builder().token(TOKEN).build()

# Commande test
async def scan_test_command(update, context):
    await update.message.reply_text("✅ Commande /scan_test reçue\n\n🚀 Début du scan test")
    results = await run_test_scan(context.bot)
    if results:
        await update.message.reply_text(f"🧠 Résultats:\n\n" + "\n\n".join(results))
    else:
        await update.message.reply_text("✅ Scan test terminé\n\nAucun signal détecté.")

application.add_handler(CommandHandler("scan_test", scan_test_command))

# Planification automatique toutes les 10 minutes
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

# Lancer le bot Telegram
async def run_bot():
    await application.run_polling()

# Exécution
if __name__ == '__main__':
    threading.Thread(target=run_flask).start()
    loop = asyncio.get_event_loop()
    loop.create_task(run_bot())
    loop.run_forever()
