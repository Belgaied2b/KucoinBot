# main.txt (anciennement main.py)

import logging
from telegram import Bot
from telegram.ext import Application, ContextTypes
from telegram.ext import JobQueue
from apscheduler.schedulers.background import BackgroundScheduler
from flask import Flask
import threading
import asyncio
from scanner import scan_and_send_signals
from config import TOKEN, CHAT_ID

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = Flask(__name__)

@app.route('/')
def home():
    return "Bot is running!"

def run_flask():
    app.run(host='0.0.0.0', port=3000)

async def start_bot():
    application = Application.builder().token(TOKEN).build()

    scheduler = BackgroundScheduler()
    scheduler.add_job(scan_and_send_signals, 'interval', hours=1, args=[application.bot])
    scheduler.start()

    await application.run_polling()

def run():
    threading.Thread(target=run_flask).start()
    asyncio.run(start_bot())

if __name__ == '__main__':
    run()