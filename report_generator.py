import os
import telegram
from performance_tracker import compute_statistics
from datetime import datetime

TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")
bot = telegram.Bot(token=TELEGRAM_TOKEN)

def send_weekly_report():
    stats = compute_statistics()
    now = datetime.utcnow().strftime("%d/%m/%Y")

    message = (
        f"📈 *Rapport Hebdomadaire – {now}*\n\n"
        f"🔹 Total signaux : {stats['total_signals']}\n"
        f"✅ TP atteints : {stats['TP']}\n"
        f"❌ SL touchés : {stats['SL']}\n"
        f"📊 Winrate : *{stats['winrate']}%*\n"
        f"🕒 Trades ouverts : {stats['open']}\n\n"
        f"#Rapport #SwingBot"
    )

    bot.send_message(chat_id=TELEGRAM_CHAT_ID, text=message, parse_mode="Markdown")
