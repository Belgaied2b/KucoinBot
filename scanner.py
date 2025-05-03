import logging
from kucoin_utils import get_kucoin_perps, fetch_klines
from signal_analysis import analyze_market
from telegram import Bot

logger = logging.getLogger(__name__)

async def scan_and_send_signals(bot: Bot):
    logger.info("🚀 Scan automatique lancé")
    symbols = get_kucoin_perps()
    logger.info(f"🔍 {len(symbols)} PERP détectés")
    for symbol in symbols:
        try:
            df = fetch_klines(symbol)
            signal = analyze_market(symbol, df)
            if signal:
                await bot.send_message(chat_id=os.environ["CHAT_ID"], text=signal)
        except Exception as e:
            logger.error(f"Erreur {symbol} : {e}")
    logger.info("✅ Scan automatique terminé")

async def run_test_scan(bot: Bot):
    logger.info("🚀 Scan test lancé")
    messages = []
    symbols = get_kucoin_perps()
    logger.info(f"🔍 {len(symbols)} PERP détectés")
    for symbol in symbols[:20]:
        try:
            df = fetch_klines(symbol)
            signal = analyze_market(symbol, df)
            if signal:
                messages.append(signal)
        except Exception as e:
            logger.error(f"Erreur {symbol} : {e}")
    logger.info("✅ Scan test terminé")
    return messages
