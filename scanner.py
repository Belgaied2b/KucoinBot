# scanner.py

import os
import logging
import httpx

from telegram import InputFile
from kucoin_utils    import BASE_URL, get_kucoin_perps, fetch_klines, get_account_balance
from signal_analysis import analyze_market
from plot_signal     import generate_trade_graph

logger = logging.getLogger(__name__)

LOW_TF   = "1hour"
HIGH_TF  = "4hour"
RISK_PCT = 0.01

# États anti‐doublons
sent_anticipation = set()
sent_zone         = set()
sent_alert        = set()

async def scan_and_send_signals(bot):
    symbols = get_kucoin_perps()
    total   = len(symbols)
    logger.info(f"🔍 Scan de {total} symboles (LOW_TF={LOW_TF}, HIGH_TF={HIGH_TF})")

    accepted_l = accepted_s = 0

    for symbol in symbols:
        try:
            # 1) Analyse sur LOW_TF
            df_low = fetch_klines(symbol, interval=LOW_TF, limit=200)
            res_low_long  = analyze_market(symbol, df_low, side="long")
            res_low_short = analyze_market(symbol, df_low, side="short")
            if not (res_low_long or res_low_short):
                # pas de zone OTE+FVG+RSI/MACD+trend .
                continue

            # Anticipation (on réutilise res_low)
            res = res_low_long or res_low_short
            if symbol not in sent_anticipation:
                buf = generate_trade_graph(symbol, df_low, res)
                await bot.send_photo(
                    chat_id=os.environ["CHAT_ID"],
                    photo=InputFile(buf, f"{symbol}.png"),
                    caption=(
                        f"⏳ Anticipation {'LONG' if res_low_long else 'SHORT'} {symbol}\n"
                        f"Entrée : {res['entry_min']:.4f}→{res['entry_max']:.4f}\n"
                        f"⚠️ Le prix n'est pas encore dans la zone."
                    )
                )
                sent_anticipation.add(symbol)

            # 2) Confirmation prix dans la zone
            price = df_low["close"].iat[-1]
            if (symbol not in sent_zone
               and res["entry_min"] <= price <= res["entry_max"]):
                await bot.send_message(
                    chat_id=os.environ["CHAT_ID"],
                    text=(
                        f"🚨 Zone atteinte {'LONG' if res_low_long else 'SHORT'} {symbol}\n"
                        f"Prix {price:.4f} dans [{res['entry_min']:.4f}-{res['entry_max']:.4f}]"
                    )
                )
                sent_zone.add(symbol)

            # 3) Confirmation multi-TF sur HIGH_TF
            df_high = fetch_klines(symbol, interval=HIGH_TF, limit=50)
            res_high = (
                analyze_market(symbol, df_high, side="long")
                or analyze_market(symbol, df_high, side="short")
            )
            if not res_high:
                continue

            # 4) Signal final
            if symbol not in sent_alert:
                cap     = get_account_balance(symbol)
                risk    = cap * RISK_PCT
                ep, sl  = res["entry_price"], res["stop_loss"]
                size    = risk / abs(ep - sl)
                buf     = generate_trade_graph(symbol, df_low, res)
                await bot.send_document(
                    chat_id=os.environ["CHAT_ID"],
                    document=InputFile(buf, filename="signal.png"),
                    caption=(
                        f"{'🟢 LONG' if res_low_long else '🔻 SHORT'} {symbol}\n"
                        f"Entry : {res['entry_price']:.4f}\n"
                        f"SL    : {res['stop_loss']:.4f}\n"
                        f"TP1   : {res['tp1']:.4f}\n"
                        f"TP2   : {res['tp2']:.4f}\n"
                        f"Taille : {size:.4f}"
                    )
                )
                sent_alert.add(symbol)
                if res_low_long:
                    accepted_l += 1
                else:
                    accepted_s += 1

        except Exception as e:
            logger.error(f"❌ Erreur sur {symbol} : {e}")

    logger.info("📊 Récap Signaux")
    logger.info(f"• LONGs envoyés  : {accepted_l}")
    logger.info(f"• SHORTs envoyés : {accepted_s}")
