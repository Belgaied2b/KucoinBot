import logging
from kucoin_utils import get_kucoin_perps, fetch_klines
from signal_analysis import analyze_market
from telegram.constants import ParseMode

logger = logging.getLogger(__name__)

async def scan_and_send_signals(bot):
    logger.info("🚀 Scan automatique lancé")
    symbols = get_kucoin_perps()
    logger.info(f"📉 Nombre de PERP détectés : {len(symbols)}")
    for symbol in symbols:
        try:
            df = fetch_klines(symbol)
            signal = analyze_market(symbol, df)
            if signal:
                await bot.send_photo(
                    chat_id=os.getenv("CHAT_ID"),
                    photo=open(signal["graph_path"], "rb"),
                    caption=signal["message"],
                    parse_mode=ParseMode.HTML
                )
        except Exception as e:
            logger.error(f"Erreur sur {symbol} : {e}")
    logger.info("✅ Scan automatique terminé")

async def run_test_scan(bot):
    logger.info("🚀 Scan test lancé")
    symbols = get_kucoin_perps()
    logger.info(f"📉 Nombre de PERP détectés : {len(symbols)}")
    for symbol in symbols[:5]:  # On teste sur 5 pour pas surcharger
        try:
            df = fetch_klines(symbol)
            signal = analyze_market(symbol, df)
            if signal:
                await bot.send_photo(
                    chat_id=os.getenv("CHAT_ID"),
                    photo=open(signal["graph_path"], "rb"),
                    caption=signal["message"],
                    parse_mode=ParseMode.HTML
                )
        except Exception as e:
            logger.error(f"Erreur sur {symbol} : {e}")
    logger.info("✅ Scan test terminé")
