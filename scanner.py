import os
import logging
from kucoin_utils import get_kucoin_perps, fetch_klines
from signal_analysis import analyze_market

# Seuil d’anticipation (0,3 %)
ANTICIPATION_THRESHOLD = 0.003

logger = logging.getLogger(__name__)

async def scan_and_send_signals(bot):
    # 1) Liste des contracts KuCoin Futures (REST)
    symbols = get_kucoin_perps()
    logger.info(f"🔍 {len(symbols)} contracts KuCoin détectés")

    for symbol in symbols:
        try:
            # 2) Récup OHLCV via REST
            df = fetch_klines(symbol)
            last_price = df["close"].iat[-1]

            # ─── LONG ───
            res_long = analyze_market(symbol, df, side="long")
            if res_long:
                el_min, el_max = res_long["entry_min"], res_long["entry_max"]

                # 2.1 Anticipation
                if el_min * (1 - ANTICIPATION_THRESHOLD) <= last_price < el_min:
                    await bot.send_message(
                        chat_id=os.environ["CHAT_ID"],
                        text=(
                            f"⏳ Anticipation LONG {symbol}\n"
                            f"Zone : {el_min:.4f} → {el_max:.4f}\n"
                            f"Prix : {last_price:.4f}"
                        )
                    )
                # 2.2 Zone atteinte
                if el_min <= last_price <= el_max:
                    await bot.send_message(
                        chat_id=os.environ["CHAT_ID"],
                        text=(
                            f"🚨 Zone de LONG atteinte {symbol}\n"
                            f"Entrée possible : {el_min:.4f}–{el_max:.4f}\n"
                            f"Prix : {last_price:.4f}"
                        )
                    )
                # 2.3 Signal final
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
                es_max, es_min = res_short["entry_max"], res_short["entry_min"]

                # 3.1 Anticipation
                if es_max <= last_price <= es_max * (1 + ANTICIPATION_THRESHOLD):
                    await bot.send_message(
                        chat_id=os.environ["CHAT_ID"],
                        text=(
                            f"⏳ Anticipation SHORT {symbol}\n"
                            f"Zone : {es_max:.4f} → {es_min:.4f}\n"
                            f"Prix : {last_price:.4f}"
                        )
                    )
                # 3.2 Zone atteinte
                if es_max >= last_price >= es_min:
                    await bot.send_message(
                        chat_id=os.environ["CHAT_ID"],
                        text=(
                            f"🚨 Zone de SHORT atteinte {symbol}\n"
                            f"Entrée possible : {es_max:.4f}–{es_min:.4f}\n"
                            f"Prix : {last_price:.4f}"
                        )
                    )
                # 3.3 Signal final
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
