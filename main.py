import logging
import asyncio
import threading
from flask import Flask
from telegram.ext import Application, CommandHandler
from scanner import scan_and_send_signals, run_test_scan
from config import TOKEN

# Logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Flask pour keep-alive
app = Flask(__name__)

@app.route('/')
def home():
    return "Bot is running!"

def run_flask():
    app.run(host='0.0.0.0', port=3000)

# Fonction principale async
async def run_bot():
    application = Application.builder().token(TOKEN).build()

    # Supprimer le webhook en cas de conflit
    await application.bot.delete_webhook(drop_pending_updates=True)

    # Ajout de la commande /scan_test
    application.add_handler(CommandHandler("scan_test", scan_test_command))

    # Démarrage propre
    logger.info("🚀 Bot démarré avec scan automatique toutes les 10 minutes")
    await application.run_polling()

# Callback /scan_test
async def scan_test_command(update, context):
    await update.message.reply_text("✅ Commande /scan_test reçue\n\n🚀 Début du scan test")
    await run_test_scan(context.bot)
    await update.message.reply_text("✅ Scan test terminé")

# Lancement
if __name__ == '__main__':
    threading.Thread(target=run_flask).start()
    asyncio.run(run_bot())
