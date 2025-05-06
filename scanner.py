# scanner.py

import os
import logging
import pandas as pd
from kucoin_utils import get_kucoin_perps, fetch_klines
from signal_analysis import analyze_market

# Distance % pour l’alerte d’anticipation
ANTICIPATION_THRESHOLD = 0.003

logger = logging.getLogger(__name__)

async def scan_and_send_signals(bot):
    # 1) Récupère la liste des contracts KuCoin Futures via REST
    symbols = get_kucoin_perps()
    logger.info(f"🔍 Démarrage du scan — {len(symbols)} contracts détectés")

    for symbol in symbols:
        try:
            # 2) Fetch OHLCV REST
            df = fetch_klines(symbol)
            last_price = df["close"].iat[-1]
            logger.info(f"{symbol} — last_price = {last_price:.4f}")

            # ─── LONG ───
            res_long = analyze_market(symbol, df, side="long")
            if res_long:
                emn, emx = res_long["entry_min"], res_long["entry_max"]
                logger.info(
                    f"{symbol} LONG zone {emn:.4f}-{emx:.4f}, "
                    f"entry={res_long['entry_price']:.4f}, "
                    f"SL={res_long['stop_loss']:.4f}, "
                    f"TP1={res_long['tp1']:.4f}, TP2={res_long['tp2']:.4f}"
                )

                # Anticipation
                if emn * (1 - ANTICIPATION_THRESHOLD) <= last_price < emn:
                    await bot.send_message(
                        chat_id=os.environ["CHAT_ID"],
                        text=f"⏳ Anticipation LONG {symbol} — zone {emn:.4f}→{emx:.4f}, prix {last_price:.4f}"
                    )
                # Zone atteinte
                if emn <= last_price <= emx:
                    await bot.send_message(
                        chat_id=os.environ["CHAT_ID"],
                        text=f"🚨 Zone LONG atteinte {symbol} — entrée possible {emn:.4f}–{emx:.4f}, prix {last_price:.4f}"
                    )
                # Signal final
                await bot.send_message(
                    chat_id=os.environ["CHAT_ID"],
                    text=(
                        f"🟢 LONG {symbol}\n"
                        f"Entry : {res_long['entry_price']:.4f}\n"
                        f"SL    : {res_long['stop_loss']:.4f}\n"
                        f"TP1   : {res_long['tp1']:.4f}\n"
                        f"TP2   : {res_long['tp2']:.4f}"
                    )
                )

            # ─── SHORT ───
            res_short = analyze_market(symbol, df, side="short")
            if res_short:
                smx, smn = res_short["entry_max"], res_short["entry_min"]
                logger.info(
                    f"{symbol} SHORT zone {smx:.4f}-{smn:.4f}, "
                    f"entry={res_short['entry_price']:.4f}, "
                    f"SL={res_short['stop_loss']:.4f}, "
                    f"TP1={res_short['tp1']:.4f}, TP2={res_short['tp2']:.4f}"
                )

                # Anticipation
                if smx <= last_price <= smx * (1 + ANTICIPATION_THRESHOLD):
                    await bot.send_message(
                        chat_id=os.environ["CHAT_ID"],
                        text=f"⏳ Anticipation SHORT {symbol} — zone {smx:.4f}→{smn:.4f}, prix {last_price:.4f}"
                    )
                # Zone atteinte
                if smx >= last_price >= smn:
                    await bot.send_message(
                        chat_id=os.environ["CHAT_ID"],
                        text=f"🚨 Zone SHORT atteinte {symbol} — entrée possible {smx:.4f}–{smn:.4f}, prix {last_price:.4f}"
                    )
                # Signal final
                await bot.send_message(
                    chat_id=os.environ["CHAT_ID"],
                    text=(
                        f"🔻 SHORT {symbol}\n"
                        f"Entry : {res_short['entry_price']:.4f}\n"
                        f"SL    : {res_short['stop_loss']:.4f}\n"
                        f"TP1   : {res_short['tp1']:.4f}\n"
                        f"TP2   : {res_short['tp2']:.4f}"
                    )
                )

        except Exception as e:
            logger.error(f"❌ Erreur sur {symbol} : {e}")
