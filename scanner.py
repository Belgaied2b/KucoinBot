import logging
import os
from kucoin_utils import get_kucoin_perps, fetch_klines
from signal_analysis import analyze_market
from plot_signal import generate_trade_graph
from telegram import Bot, InputFile

logger = logging.getLogger(__name__)

async def scan_and_send_signals(bot: Bot):
    logger.info("🚀 Scan automatique lancé")
    symbols = get_kucoin_perps()
    logger.info(f"🔍 {len(symbols)} PERP détectés")
    for symbol in symbols:
        try:
            logger.info(f"🔎 Analyse de {symbol}...")
            df = fetch_klines(symbol)
            result = analyze_market(symbol, df)
            if result:
                buf = generate_trade_graph(symbol, df, result)
                await bot.send_photo(chat_id=os.environ["CHAT_ID"], photo=InputFile(buf))
                logger.info(f"📈 SIGNAL détecté et envoyé pour {symbol}")
            else:
                # Log valeurs RSI / MACD
                rsi = df["rsi"].iloc[-1] if "rsi" in df.columns else None
                macd = df["macd"].iloc[-1] if "macd" in df.columns else None
                signal = df["signal"].iloc[-1] if "signal" in df.columns else None
                logger.info(f"❌ {symbol} → Aucun signal | RSI: {rsi:.2f} | MACD: {macd:.4f} | Signal: {signal:.4f}")
        except Exception as e:
            logger.error(f"❌ Erreur avec {symbol} : {e}")
    logger.info("✅ Scan automatique terminé")

async def run_test_scan(bot: Bot):
    logger.info("🚀 Scan test lancé")
    symbols = get_kucoin_perps()
    logger.info(f"🔍 {len(symbols)} PERP détectés")
    for symbol in symbols:
        logger.info(f"🔎 Test de {symbol}...")
        try:
            df = fetch_klines(symbol)
            result = analyze_market(symbol, df)
            if result:
                logger.info(f"[SIGNAL] {symbol} - Entrée : {result['entry']} | SL : {result['sl']} | TP : {result['tp']}")
            else:
                # Log valeurs RSI / MACD pour debug
                rsi = df["rsi"].iloc[-1] if "rsi" in df.columns else None
                macd = df["macd"].iloc[-1] if "macd" in df.columns else None
                signal = df["signal"].iloc[-1] if "signal" in df.columns else None
                logger.info(f"❌ {symbol} → Aucun signal | RSI: {rsi:.2f} | MACD: {macd:.4f} | Signal: {signal:.4f}")
        except Exception as e:
            logger.error(f"❌ Erreur avec {symbol} : {e}")
    logger.info("✅ Scan test terminé")
