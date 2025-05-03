import os
import logging
from telegram import Bot, InputFile
from kucoin_utils import get_kucoin_perps, fetch_klines
from signal_analysis import analyze_market
from plot_signal import generate_trade_graph

logger = logging.getLogger(__name__)

# Mémoire temporaire pour suivre les signaux déjà annoncés
active_signals = {}

async def scan_and_send_signals(bot: Bot):
    """
    Scan automatique : récupère tous les PERP, applique l'analyse,
    et envoie un graphique pour chaque nouveau signal détecté,
    puis une alerte quand le prix entre vraiment dans la zone.
    """
    logger.info("🚀 Scan automatique lancé")
    symbols = get_kucoin_perps()
    logger.info(f"🔍 {len(symbols)} PERP détectés")

    for symbol in symbols:
        try:
            df     = fetch_klines(symbol)
            result = analyze_market(symbol, df)
            if not result:
                continue

            # 1) Envoi du preview si jamais annoncé
            if symbol not in active_signals:
                buf       = generate_trade_graph(symbol, df, result)
                input_f   = InputFile(buf, filename=f"{symbol}.png")
                ote_zone  = result.get("ote_zone")
                fvg_zone  = result.get("fvg_zone")
                caption   = (
                    f"🧠 *Signal anticipé* pour {symbol}\n"
                    f"Entrée idéale : `{result['entry']}`\n"
                    f"SL : `{result['sl']}` | TP : `{result['tp']}`"
                )
                if ote_zone:
                    caption += f"\nZone OTE : {ote_zone}"
                if fvg_zone:
                    caption += f"\nZone FVG : {fvg_zone}"
                caption += "\n⚠️ Le prix n'est *pas encore* dans la zone."

                await bot.send_photo(
                    chat_id=os.environ["CHAT_ID"],
                    photo=input_f,
                    caption=caption,
                    parse_mode="Markdown"
                )
                active_signals[symbol] = result

            # 2) Alerte quand le prix entre réellement dans les deux zones
            current_price = df["close"].iloc[-1]
            if (
                not result.get("active")
                and result.get("ote_zone")
                and result.get("fvg_zone")
                and result["ote_zone"][0] <= current_price <= result["ote_zone"][1]
                and result["fvg_zone"][0] <= current_price <= result["fvg_zone"][1]
            ):
                result["active"] = True
                await bot.send_message(
                    chat_id=os.environ["CHAT_ID"],
                    text=(
                        f"🚨 *ALERTE URGENTE* 🚨\n"
                        f"Le prix de {symbol} est entré dans la *zone idéale* !\n"
                        f"🎯 Entrée : `{result['entry']}`\n"
                        f"SL : `{result['sl']}` | TP : `{result['tp']}`"
                    ),
                    parse_mode="Markdown"
                )

        except Exception as e:
            logger.error(f"❌ Erreur sur {symbol} : {e}")

    logger.info("✅ Scan automatique terminé")


async def run_test_scan(bot: Bot):
    """
    Scan de test : récupère tous les PERP, applique l'analyse,
    et renvoie la liste des messages de signaux (sans envoi Telegram).
    """
    logger.info("🚀 Scan test lancé")
    symbols = get_kucoin_perps()
    logger.info(f"🔍 {len(symbols)} PERP détectés")
    messages = []

    for symbol in symbols:
        try:
            df     = fetch_klines(symbol)
            result = analyze_market(symbol, df)
            if result:
                messages.append(
                    f"[SIGNAL] {symbol} - Entrée : {result['entry']} | "
                    f"SL : {result['sl']} | TP : {result['tp']}"
                )
        except Exception as e:
            logger.error(f"❌ Erreur sur {symbol} : {e}")

    logger.info("✅ Scan test terminé")
    return messages
