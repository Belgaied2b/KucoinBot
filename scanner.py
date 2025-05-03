# scanner.py

import logging
from kucoin_utils import get_kucoin_perps, fetch_klines
from signal_analysis import analyze_market
from plot_signal import generate_trade_graph
from telegram import InputFile

logger = logging.getLogger(__name__)

async def scan_and_send_signals(bot):
    print("📡 Scan automatique démarré")
    symbols = get_kucoin_perps()
    print(f"🔍 Analyse de {len(symbols)} contrats PERP...")

    for symbol in symbols:
        df = fetch_klines(symbol)
        if df is None:
            continue
        signal = analyze_market(symbol, df)
        if signal:
            image_path = generate_trade_graph(df, signal)
            await bot.send_photo(
                chat_id=CHAT_ID,
                photo=InputFile(image_path),
                caption=signal["message"]
            )

    print("✅ Scan automatique terminé")

async def run_test_scan(bot):
    print("📡 Scan test démarré")
    symbols = get_kucoin_perps()
    print(f"🔍 Analyse de {len(symbols)} contrats PERP...")

    count = 0
    for symbol in symbols:
        df = fetch_klines(symbol)
        if df is None:
            continue
        signal = analyze_market(symbol, df)
        if signal:
            count += 1
            image_path = generate_trade_graph(df, signal)
            await bot.send_photo(
                chat_id=CHAT_ID,
                photo=InputFile(image_path),
                caption=signal["message"]
            )

    print(f"✅ Scan test terminé — {count} signal(s) détecté(s)")
