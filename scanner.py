import os
import pandas as pd
import logging
from telegram import InputFile
from kucoin_utils import get_kucoin_perps, fetch_klines
from analysis import analyze_symbol
from plot_signal import generate_trade_graph
from config import CHAT_ID

# Logger
logger = logging.getLogger(__name__)

async def scan_and_send_signals(bot):
    print("🚀 Début du scan automatique")

    try:
        perps = get_kucoin_perps()
        print(f"📉 Nombre de PERP détectés : {len(perps)}")

        for symbol in perps:
            df = fetch_klines(symbol)
            if df is None or df.empty:
                continue

            signal = analyze_symbol(symbol, df)
            if signal:
                print(f"✅ Signal détecté : {symbol}")

                image_path = generate_trade_graph(
                    df,
                    signal["entry"],
                    signal["sl"],
                    signal["tp"],
                    signal["side"],
                    symbol
                )

                caption = (
                    f"📈 *{symbol}* - *{signal['side'].upper()}*\n"
                    f"🎯 Entrée : `{signal['entry']:.4f}`\n"
                    f"🛑 SL : `{signal['sl']:.4f}`\n"
                    f"🎯 TP : `{signal['tp']:.4f}`"
                )

                with open(image_path, 'rb') as img:
                    await bot.send_photo(chat_id=CHAT_ID, photo=InputFile(img), caption=caption, parse_mode='Markdown')

                os.remove(image_path)
    except Exception as e:
        logger.error(f"Erreur pendant le scan : {e}")

    print("✅ Scan terminé")
