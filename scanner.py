# scanner.py

import os
import logging
from telegram import InputFile
from kucoin_utils import get_kucoin_perps, fetch_klines
from signal_analysis import analyze_market
from plot_signal import generate_trade_graph

logger = logging.getLogger(__name__)

sent_anticipation = set()
sent_alert = set()

async def scan_and_send_signals(bot):
    symbols = get_kucoin_perps()
    logger.info(f"🔍 {len(symbols)} PERP détectés")

    for symbol in symbols:
        try:
            df = fetch_klines(symbol)
            logger.info(f"🔎 Analyse de {symbol} en cours...")
            result = analyze_market(symbol, df)

            if not result:
                sent_anticipation.discard(symbol)
                sent_alert.discard(symbol)
                continue

            if symbol not in sent_anticipation:
                buf = generate_trade_graph(symbol, df, result)
                photo = InputFile(buf, filename=f"{symbol}.png")
                ote_low, ote_high = result['ote_zone']
                fvg_low, fvg_high = result['fvg_zone']

                await bot.send_photo(
                    chat_id=os.environ["CHAT_ID"],
                    photo=photo,
                    caption=(
                        f"🧠 *Signal anticipé* pour {symbol}\n"
                        f"Entrée : `{result['entry']}` | SL : `{result['sl']}` | TP : `{result['tp']}`\n"
                        f"OTE zone : {ote_low:.4f} – {ote_high:.4f}\n"
                        f"FVG zone : {fvg_low:.4f} – {fvg_high:.4f}"
                    ),
                    parse_mode='Markdown'
                )
                sent_anticipation.add(symbol)

        except Exception as e:
            logger.error(f"Erreur analyse {symbol} : {e}")
