import logging
import os
import threading
from flask import Flask
from telegram import Update
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    ContextTypes,
)

from scanner import scan_and_send_signals
from analyse_stats import compute_stats

# Configuration
TOKEN   = os.environ["TOKEN"]
CHAT_ID = os.environ["CHAT_ID"]

# Logger
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Flask keep-alive
app = Flask(__name__)
@app.route("/")
def home():
    return "Bot is running!"

# --- Commandes Telegram ---
async def scan_test_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    logger.info("✅ Commande /scan_test reçue")
    await update.message.reply_text("Scan en cours…")
    await scan_and_send_signals(context.bot)

async def scan_graph_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    logger.info("✅ Commande /scan_graph reçue")
    await update.message.reply_text("🚀 Envoi des signaux anticipés…")
    await scan_and_send_signals(context.bot)

# --- Job principal : scan + stats ---
async def scheduled_scan(context: ContextTypes.DEFAULT_TYPE):
    logger.info("⏰ Début du job — scan automatique")
    await scan_and_send_signals(context.bot)

    # **Juste après le scan**, on calcule et logge les stats
    # Baisse le bruit des logs API
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("kucoin_utils").setLevel(logging.WARNING)

    df_stats, df_means = compute_stats(limit=50)

    # Log détaillé (extrait)
    logger.warning(
        "🔢 Stats détaillées (20 premières lignes):\n%s",
        df_stats.to_string(index=False, max_rows=20)
    )
    # Log des moyennes
    logger.warning(
        "📊 Moyennes (Signal vs No-Signal):\n%s",
        df_means.to_string(index=False)
    )

    # Taux de passage / blocage
    pass_rates  = df_stats[['rsi_ok','macd_ok','ote_ok','fvg_ok']].mean()
    block_rates = 1 - pass_rates
    blocker     = block_rates.idxmax()
    blocker_pct = block_rates[blocker] * 100

    logger.warning(
        "❌ Indicateur qui bloque le plus de signaux : %s (%.2f%% de rejets)",
        blocker, blocker_pct
    )
    logger.warning(
        "✅ Taux de passage : RSI %.2f%%, MACD %.2f%%, OTE %.2f%%, FVG %.2f%%",
        pass_rates['rsi_ok']*100,
        pass_rates['macd_ok']*100,
        pass_rates['ote_ok']*100,
        pass_rates['fvg_ok']*100
    )

def main():
    application = ApplicationBuilder().token(TOKEN).build()

    # Handlers
    application.add_handler(CommandHandler("scan_test",  scan_test_command))
    application.add_handler(CommandHandler("scan_graph", scan_graph_command))

    # Scan + stats toutes les 10 minutes (premier run 5s après démarrage)
    application.job_queue.run_repeating(scheduled_scan, interval=600, first=5)

    # Flask keep-alive en arrière-plan
    threading.Thread(
        target=lambda: app.run(
            host="0.0.0.0",
            port=int(os.environ.get("PORT", 3000))
        ),
        daemon=True
    ).start()

    logger.info("🚀 Bot démarré — scan+stats auto toutes les 10 min")
    application.run_polling()

if __name__ == "__main__":
    main()
