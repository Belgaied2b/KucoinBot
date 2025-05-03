# scanner.py

import os
import logging
from telegram import InputFile
from kucoin_utils import get_kucoin_perps, fetch_klines
from signal_analysis import analyze_market
from plot_signal import generate_trade_graph

logger = logging.getLogger(__name__)

# État des signaux en mémoire pour éviter les doublons
# Structure: { symbol: { 'entry','sl','tp','ote_zone','fvg_zone','alerted': bool } }
active_signals = {}

async def scan_and_send_signals(bot):
    symbols = get_kucoin_perps()
    for symbol in symbols:
        try:
            df = fetch_klines(symbol)
            result = analyze_market(symbol, df)

            # 1) Si plus de signal pour ce symbole, on le réinitialise
            if not result:
                if symbol in active_signals:
                    del active_signals[symbol]
                continue

            # 2) Nouveau signal → envoi du signal anticipé
            if symbol not in active_signals:
                buf = generate_trade_graph(symbol, df, result)
                input_file = InputFile(buf, filename=f"{symbol}.png")
                await bot.send_photo(
                    chat_id=os.environ["CHAT_ID"],
                    photo=input_file,
                    caption=(
                        f"🧠 *Signal anticipé* pour {symbol}\n"
                        f"Entrée idéale : `{result['entry']}` | SL : `{result['sl']}` | TP : `{result['tp']}`\n"
                        f"Zone OTE : {result['ote_zone']} | Zone FVG : {result['fvg_zone']}\n"
                        f"⚠️ Le prix n'est *pas encore* dans la zone."
                    ),
                    parse_mode="Markdown"
                )
                # On stocke l'état et on marque 'alerted' à False
                active_signals[symbol] = { **result, "alerted": False }

            # 3) Vérification de l'alerte urgente si pas déjà envoyée
            state = active_signals[symbol]
            if not state["alerted"]:
                current_price = df["close"].iat[-1]
                ote_low, ote_high = state["ote_zone"]
                fvg_low, fvg_high = state["fvg_zone"]

                if ote_low <= current_price <= ote_high and fvg_low <= current_price <= fvg_high:
                    await bot.send_message(
                        chat_id=os.environ["CHAT_ID"],
                        text=(
                            f"🚨 *ALERTE URGENTE* 🚨\n"
                            f"{symbol} est **ENTRÉ** dans la zone idéale !\n"
                            f"🎯 Entrée : `{state['entry']}` | SL : `{state['sl']}` | TP : `{state['tp']}`"
                        ),
                        parse_mode="Markdown"
                    )
                    # On marque l'alerte comme envoyée pour ne pas la renvoyer
                    active_signals[symbol]["alerted"] = True

        except Exception as e:
            logger.error(f"❌ Erreur sur {symbol} : {e}")
