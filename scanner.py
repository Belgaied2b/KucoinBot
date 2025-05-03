import os
import logging
from telegram import Bot, InputFile
from kucoin_utils import get_kucoin_perps, fetch_klines
from signal_analysis import analyze_market
from plot_signal import generate_trade_graph

logger = logging.getLogger(__name__)

async def scan_and_send_signals(bot: Bot):
    """
    Scan automatique : récupère tous les PERP, applique l'analyse,
    et envoie un graphique pour chaque signal LONG ou SHORT détecté.
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

            # Génère et envoie le graphique
            buf = generate_trade_graph(symbol, df, result)
            photo = InputFile(buf, filename=f"{symbol}.png")

            caption = (
                f"📊 *Signal {result['side']}* pour {symbol}\n"
                f"🎯 Entrée : `{result['entry']}`\n"
                f"🔻 SL : `{result['sl']}` | 🔺 TP : `{result['tp']}`"
            )
            await bot.send_photo(
                chat_id=os.environ["CHAT_ID"],
                photo=photo,
                caption=caption,
                parse_mode="Markdown"
            )
            logger.info(f"📈 SIGNAL envoyé pour {symbol} ({result['side']})")

        except Exception as e:
            logger.error(f"❌ Erreur sur {symbol} : {e}")

    logger.info("✅ Scan automatique terminé")


async def run_test_scan(bot: Bot):
    """
    Scan de test : récupère tous les PERP, applique l'analyse,
    et renvoie une liste de messages (sans envoi Telegram) pour te permettre
    de vérifier rapidement ce qui passe ou non.
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
                    f"[SIGNAL] {symbol} – {result['side']} | "
                    f"Entrée: {result['entry']} | SL: {result['sl']} | TP: {result['tp']}"
                )
            else:
                messages.append(f"❌ {symbol} – Aucun signal")
        except Exception as e:
            logger.error(f"❌ Erreur sur {symbol} : {e}")

    logger.info("✅ Scan test terminé")
    return messages
