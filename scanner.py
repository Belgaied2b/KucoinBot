import os
import logging
from telegram import InputFile
from kucoin_utils import fetch_klines
from signal_analysis import analyze_market
from plot_signal import generate_trade_graph

logger = logging.getLogger(__name__)

# Mémoire temporaire pour suivre les signaux actifs
active_signals = {}

async def scan_and_send_signals(bot):
    from kucoin_utils import get_kucoin_perps
    symbols = get_kucoin_perps()
    for symbol in symbols:
        try:
            df = fetch_klines(symbol)
            result = analyze_market(symbol, df)
            if not result:
                continue

            # Prévisualisation envoyée
            if symbol not in active_signals:
                buf = generate_trade_graph(symbol, df, result)
                input_file = InputFile(buf, filename=f"{symbol}.png")
                ote_zone = result["ote_zone"]
                fvg_zone = result["fvg_zone"]
                await bot.send_photo(
                    chat_id=os.environ["CHAT_ID"],
                    photo=input_file,
                    caption=(
                        f"🧠 *Signal anticipé* pour {symbol}\n"
                        f"Entrée idéale : `{result['entry']}`\n"
                        f"SL : `{result['sl']}` | TP : `{result['tp']}`\n"
                        f"Zone OTE : {ote_zone}\n"
                        f"Zone FVG : {fvg_zone}\n"
                        f"⚠️ Le prix n'est *pas encore* dans la zone."
                    ),
                    parse_mode='Markdown'
                )
                active_signals[symbol] = result

            # Vérifie si le prix actuel est dans la zone
            current_price = df["close"].iloc[-1]
            if (not result["active"] and
                result["ote_zone"][0] <= current_price <= result["ote_zone"][1] and
                result["fvg_zone"][0] <= current_price <= result["fvg_zone"][1]):
                result["active"] = True
                await bot.send_message(
                    chat_id=os.environ["CHAT_ID"],
                    text=(
                        f"🚨 *ALERTE URGENTE* 🚨\n"
                        f"Le prix de {symbol} est entré dans la *zone idéale* !\n"
                        f"🎯 Entrée : `{result['entry']}`\n"
                        f"SL : `{result['sl']}` | TP : `{result['tp']}`"
                    ),
                    parse_mode='Markdown'
                )

        except Exception as e:
            logger.error(f"❌ Erreur sur {symbol} : {e}")
