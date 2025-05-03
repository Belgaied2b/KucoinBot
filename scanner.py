# scanner.py

import logging
from kucoin_utils import get_kucoin_perps, fetch_klines
from signal_analysis import analyze_market
from plot_signal import generate_trade_graph
import os

logger = logging.getLogger(__name__)
CHAT_ID = os.getenv("CHAT_ID")

async def scan_and_send_signals(bot):
    logger.info("🔄 Scan automatique lancé")
    symbols = get_kucoin_perps()
    logger.info(f"🔎 {len(symbols)} contrats PERP détectés")

    for symbol in symbols:
        df = fetch_klines(symbol)
        if df is None or df.empty:
            continue

        signal = analyze_market(symbol, df)
        if signal:
            graph_path = generate_trade_graph(symbol, df, signal)
            await bot.send_photo(chat_id=CHAT_ID, photo=open(graph_path, "rb"))
            logger.info(f"📡 Signal envoyé : {symbol}")

    logger.info("✅ Scan terminé")

async def run_test_scan(bot):
    logger.info("🚀 Scan test lancé")
    symbols = get_kucoin_perps()
    logger.info(f"🔎 {len(symbols)} contrats PERP détectés")

    results = []
    for symbol in symbols:
        df = fetch_klines(symbol)
        if df is None or df.empty:
            continue

        signal = analyze_market(symbol, df)
        if signal:
            graph_path = generate_trade_graph(symbol, df, signal)
            results.append(graph_path)

    logger.info("✅ Scan test terminé")
    return results
