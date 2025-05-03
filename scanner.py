# scanner.py

import os
import logging
from telegram import InputFile
from kucoin_utils import get_kucoin_perps, fetch_klines
from signal_analysis import analyze_market
from plot_signal import generate_trade_graph

logger = logging.getLogger(__name__)
active_signals = {}

async def scan_and_send_signals(bot):
    symbols = get_kucoin_perps()
    for symbol in symbols:
        try:
            df     = fetch_klines(symbol)
            result = analyze_market(symbol, df)
            if not result:
                continue

            # 1) Signal anticipé : envoi du graphique
            if symbol not in active_signals:
                buf       = generate_trade_graph(symbol, df, result)
                input_file = InputFile(buf, filename=f'{symbol}.png')
                ote_low, ote_high = result['ote_zone']
                fvg_low, fvg_high = result['fvg_zone']

                await bot.send_photo(
                    chat_id=os.environ['CHAT_ID'],
                    photo=input_file,
                    caption=(
                        f"🧠 *Signal anticipé* pour {symbol}\n"
                        f"Entrée : `{result['entry']}` | SL : `{result['sl']}` | TP : `{result['tp']}`\n"
                        f"Zone OTE : {result['ote_zone']} | Zone FVG : {result['fvg_zone']}\n"
                        f"⚠️ Le prix n'est *pas encore* dans la zone."
                    ),
                    parse_mode='Markdown'
                )
                active_signals[symbol] = result

            # 2) Alerte urgente : quand prix entre dans la zone
            price = df['close'].iat[-1]
            ote_low, ote_high = result['ote_zone']
            fvg_low, fvg_high = result['fvg_zone']

            if (not result['active']
                and ote_low <= price <= ote_high
                and fvg_low <= price <= fvg_high):
                result['active'] = True
                await bot.send_message(
                    chat_id=os.environ['CHAT_ID'],
                    text=(
                        f"🚨 *ALERTE URGENTE* 🚨\n"
                        f"{symbol} est **ENTRÉ** dans la zone idéale !\n"
                        f"🎯 Entrée : `{result['entry']}` | SL : `{result['sl']}` | TP : `{result['tp']}`"
                    ),
                    parse_mode='Markdown'
                )

        except Exception as e:
            logger.error(f"❌ Erreur sur {symbol} : {e}")
