import logging
import os
from kucoin_utils import get_kucoin_perps, fetch_klines, is_valid_granularity
from signal_analysis import analyze_market
from plot_signal import generate_trade_graph
from telegram import Bot, InputFile

logger = logging.getLogger(__name__)

async def scan_and_send_signals(bot: Bot):
    logger.info("🚀 Scan automatique lancé")
    symbols = get_kucoin_perps()
    logger.info(f"🔍 {len(symbols)} PERP détectés")
    for symbol in symbols:
        try:
            if not is_valid_granularity(symbol):
                continue
            df = fetch_klines(symbol)
            result = analyze_market(symbol, df)
            if result:
                buf = generate_trade_graph(symbol, df, result)
                await bot.send_photo(chat_id=os.environ["CHAT_ID"], photo=InputFile(buf))
        except Exception as e:
            logger.error(f"Erreur {symbol} : {e}")
    logger.info("✅ Scan automatique terminé")

async def run_test_scan(bot: Bot):
    logger.info("🚀 Scan test lancé")
    symbols = get_kucoin_perps()
    logger.info(f"🔍 {len(symbols)} PERP détectés")
    for symbol in symbols:
        logger.info(f"🔎 PERP détecté : {symbol}")
        try:
            if not is_valid_granularity(symbol):
                logger.info(f"⛔ {symbol} ignoré (granularité non supportée)")
                continue
            df = fetch_klines(symbol)
            result = analyze_market(symbol, df)
            if result:
                logger.info(f"[SIGNAL] {symbol} - Entrée : {result['entry']} | SL : {result['sl']} | TP : {result['tp']}")
        except Exception as e:
            logger.error(f"Erreur {symbol} : {e}")
    logger.info("✅ Scan test terminé")
