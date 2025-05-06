# scanner.py

import os
import logging

from kucoin_utils import get_kucoin_perps, fetch_klines
from signal_analysis import analyze_market
from indicators import compute_rsi, compute_macd

# Distance % pour l’alerte d’anticipation
ANTICIPATION_THRESHOLD = 0.003

logger = logging.getLogger(__name__)

async def scan_and_send_signals(bot):
    symbols = get_kucoin_perps()
    total = len(symbols)
    logger.info(f"🔍 Démarrage du scan — {total} contracts détectés")

    accepted_long = 0
    accepted_short = 0

    for symbol in symbols:
        try:
            df = fetch_klines(symbol)
            last_price = df["close"].iat[-1]

            # Calcul des indicateurs pour log
            rsi = compute_rsi(df["close"], 14).iat[-1]
            macd_line, signal_line, _ = compute_macd(df["close"])
            macd_val   = macd_line.iat[-1]
            signal_val = signal_line.iat[-1]

            logger.info(
                f"{symbol} → prix={last_price:.4f}, RSI={rsi:.2f}, "
                f"MACD={macd_val:.6f}, SIG={signal_val:.6f}"
            )

            # Test LONG
            res_long = analyze_market(symbol, df, side="long")
            if res_long:
                accepted_long += 1
                emn, emx = res_long["entry_min"], res_long["entry_max"]
                ep, sl, tp1, tp2 = (
                    res_long["entry_price"],
                    res_long["stop_loss"],
                    res_long["tp1"],
                    res_long["tp2"],
                )
                logger.info(
                    f"{symbol} LONG OK zone {emn:.4f}-{emx:.4f}, "
                    f"entry={ep:.4f}, SL={sl:.4f}, TP1={tp1:.4f}, TP2={tp2:.4f}"
                )

                # Alerte anticipation
                if emn * (1 - ANTICIPATION_THRESHOLD) <= last_price < emn:
                    await bot.send_message(
                        chat_id=os.environ["CHAT_ID"],
                        text=f"⏳ Antici LONG {symbol} zone {emn:.4f}→{emx:.4f}, prix {last_price:.4f}"
                    )
                # Alerte zone atteinte
                if emn <= last_price <= emx:
                    await bot.send_message(
                        chat_id=os.environ["CHAT_ID"],
                        text=f"🚨 Zone LONG atteinte {symbol} entrée {emn:.4f}–{emx:.4f}, prix {last_price:.4f}"
                    )
                # Signal final
                await bot.send_message(
                    chat_id=os.environ["CHAT_ID"],
                    text=(
                        f"🟢 LONG {symbol}\n"
                        f"Entry : {ep:.4f}\n"
                        f"SL    : {sl:.4f}\n"
                        f"TP1   : {tp1:.4f}\n"
                        f"TP2   : {tp2:.4f}"
                    )
                )

            # Test SHORT
            res_short = analyze_market(symbol, df, side="short")
            if res_short:
                accepted_short += 1
                smx, smn = res_short["entry_max"], res_short["entry_min"]
                ep, sl, tp1, tp2 = (
                    res_short["entry_price"],
                    res_short["stop_loss"],
                    res_short["tp1"],
                    res_short["tp2"],
                )
                logger.info(
                    f"{symbol} SHORT OK zone {smx:.4f}-{smn:.4f}, "
                    f"entry={ep:.4f}, SL={sl:.4f}, TP1={tp1:.4f}, TP2={tp2:.4f}"
                )

                # Alerte anticipation
                if smx <= last_price <= smx * (1 + ANTICIPATION_THRESHOLD):
                    await bot.send_message(
                        chat_id=os.environ["CHAT_ID"],
                        text=f"⏳ Antici SHORT {symbol} zone {smx:.4f}→{smn:.4f}, prix {last_price:.4f}"
                    )
                # Alerte zone atteinte
                if smx >= last_price >= smn:
                    await bot.send_message(
                        chat_id=os.environ["CHAT_ID"],
                        text=f"🚨 Zone SHORT atteinte {symbol} entrée {smx:.4f}–{smn:.4f}, prix {last_price:.4f}"
                    )
                # Signal final
                await bot.send_message(
                    chat_id=os.environ["CHAT_ID"],
                    text=(
                        f"🔻 SHORT {symbol}\n"
                        f"Entry : {ep:.4f}\n"
                        f"SL    : {sl:.4f}\n"
                        f"TP1   : {tp1:.4f}\n"
                        f"TP2   : {tp2:.4f}"
                    )
                )

        except Exception as e:
            logger.error(f"❌ Erreur sur {symbol} : {e}")

    # ─── Résumé du scan ───
    pct_long   = accepted_long / total * 100 if total else 0
    pct_short  = accepted_short / total * 100 if total else 0
    logger.info(
        f"📊 Résumé scan : Longs acceptés {accepted_long}/{total} "
        f"({pct_long:.1f}%), Shorts acceptés {accepted_short}/{total} ({pct_short:.1f}%)"
    )
