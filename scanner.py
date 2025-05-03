# scanner.py

import asyncio
from telegram import InputFile
from kucoin_utils import get_kucoin_perps, fetch_klines
from signal_analysis import analyze_market
from plot_signal import generate_trade_graph

async def scan_and_send_signals(bot):
    print("🔍 [AutoScan] Début du scan des PERP KuCoin...")
    results = await analyze_market(bot)
    print("✅ [AutoScan] Scan terminé.")
    if results:
        for signal in results:
            buffer = generate_trade_graph(signal["dataframe"], signal)
            if buffer:
                await bot.send_photo(
                    chat_id=signal["chat_id"],
                    photo=InputFile(buffer, filename="trade.png"),
                    caption=signal["message"]
                )
    else:
        print("❌ [AutoScan] Aucun signal détecté.")

async def run_test_scan(bot):
    print("🚀 Début du scan test")
    results = await analyze_market(bot)
    print(f"📉 Nombre de PERP détectés : {len(results)}" if results else "📉 Aucun signal détecté.")
    if results:
        for signal in results:
            buffer = generate_trade_graph(signal["dataframe"], signal)
            if buffer:
                await bot.send_photo(
                    chat_id=signal["chat_id"],
                    photo=InputFile(buffer, filename="trade.png"),
                    caption=signal["message"]
                )
    print("✅ Scan test terminé")
