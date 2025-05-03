import logging
from kucoin_utils import get_kucoin_perps, fetch_klines
from signal_analysis import analyze_market
from telegram import InputFile
from io import BytesIO

async def scan_and_send_signals(bot):
    logging.info("🚀 Début du scan auto")
    symbols = await get_kucoin_perps()
    logging.info(f"📉 Nombre de PERP détectés : {len(symbols)}")

    for symbol in symbols:
        df = await fetch_klines(symbol)
        if df is None:
            continue

        result = await analyze_market(bot, symbol, df)
        if result:
            buffer, message = result
            await bot.send_photo(chat_id=os.environ["CHAT_ID"], photo=InputFile(buffer), caption=message)

    logging.info("✅ Scan auto terminé")

async def run_test_scan(bot):
    logging.info("🚀 Scan test déclenché")
    symbols = await get_kucoin_perps()
    logging.info(f"📉 Nombre de PERP détectés : {len(symbols)}")

    for symbol in symbols[:5]:  # test sur 5 paires
        df = await fetch_klines(symbol)
        if df is None:
            continue

        result = await analyze_market(bot, symbol, df)
        if result:
            buffer, message = result
            await bot.send_photo(chat_id=os.environ["CHAT_ID"], photo=InputFile(buffer), caption=message)

    logging.info("✅ Scan test terminé")
