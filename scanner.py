# scanner.py

import os
import logging

from telegram import InputFile
from kucoin_utils import get_kucoin_perps, fetch_klines
from signal_analysis  import analyze_market
from plot_signal      import generate_trade_graph
from indicators       import compute_rsi, compute_macd

# Anticipation ±0.3%
ANTICIPATION_THRESHOLD = 0.003

logger = logging.getLogger(__name__)

# État pour éviter les doublons
sent_anticipation = set()
sent_zone        = set()
sent_alert       = set()

async def scan_and_send_signals(bot):
    symbols = get_kucoin_perps()
    total   = len(symbols)
    logger.info(f"🔍 Démarrage du scan — {total} contracts détectés")

    accepted_long = 0
    accepted_short= 0

    for symbol in symbols:
        try:
            # ─── Récup OHLCV + Indicateurs ───
            df         = fetch_klines(symbol)
            last_price = df["close"].iat[-1]
            rsi        = compute_rsi(df["close"], 14).iat[-1]
            macd_line, sig_line, _ = compute_macd(df["close"])
            macd_val, sig_val = macd_line.iat[-1], sig_line.iat[-1]

            logger.info(
                f"{symbol} → price={last_price:.4f}, RSI={rsi:.2f}, "
                f"MACD={macd_val:.6f}, SIG={sig_val:.6f}"
            )

            # ─── Test LONG ───
            res_l = analyze_market(symbol, df, side="long")
            if res_l:
                accepted_long += 1
                emn, emx = res_l["entry_min"], res_l["entry_max"]
                ep, sl, tp1, tp2 = (
                    res_l["entry_price"],
                    res_l["stop_loss"],
                    res_l["tp1"],
                    res_l["tp2"],
                )
                logger.info(
                    f"{symbol} LONG OK zone [{emn:.4f}-{emx:.4f}] "
                    f"entry={ep:.4f}, SL={sl:.4f}, TP1={tp1:.4f}, TP2={tp2:.4f}"
                )

                # 1) Anticipation LONG
                if emn*(1-ANTICIPATION_THRESHOLD) <= last_price < emn:
                    if symbol not in sent_anticipation:
                        logger.info(f"{symbol} ⏳ anticipation LONG à {last_price:.4f}")
                        await bot.send_message(
                            chat_id=os.environ["CHAT_ID"],
                            text=(
                                f"⏳ Anticipation LONG {symbol}\n"
                                f"Zone {emn:.4f} → {emx:.4f}\n"
                                f"Prix actuel : {last_price:.4f}"
                            )
                        )
                        sent_anticipation.add(symbol)
                else:
                    sent_anticipation.discard(symbol)

                # 2) Zone LONG atteinte
                if emn <= last_price <= emx:
                    if symbol not in sent_zone:
                        logger.info(f"{symbol} 🚨 zone LONG atteinte à {last_price:.4f}")
                        await bot.send_message(
                            chat_id=os.environ["CHAT_ID"],
                            text=(
                                f"🚨 Zone LONG atteinte {symbol}\n"
                                f"Entrée : {emn:.4f}–{emx:.4f}\n"
                                f"Prix actuel : {last_price:.4f}"
                            )
                        )
                        sent_zone.add(symbol)
                else:
                    sent_zone.discard(symbol)

                # 3) Signal final LONG + graph
                if symbol not in sent_alert:
                    buf = generate_trade_graph(
                        symbol,
                        df,
                        {"entry": ep, "sl": sl, "tp": tp1,
                         "fvg_zone": (emn, emx)}
                    )
                    await bot.send_document(
                        chat_id=os.environ["CHAT_ID"],
                        document=InputFile(buf, filename="signal_long.png"),
                        caption=(
                            f"🟢 LONG {symbol}\n"
                            f"Entry : {ep:.4f}\n"
                            f"SL    : {sl:.4f}\n"
                            f"TP1   : {tp1:.4f}\n"
                            f"TP2   : {tp2:.4f}"
                        )
                    )
                    sent_alert.add(symbol)
            else:
                # reset état
                sent_anticipation.discard(symbol)
                sent_zone.discard(symbol)
                sent_alert.discard(symbol)

            # ─── Test SHORT ───
            res_s = analyze_market(symbol, df, side="short")
            if res_s:
                accepted_short += 1
                smx, smn = res_s["entry_max"], res_s["entry_min"]
                ep, sl, tp1, tp2 = (
                    res_s["entry_price"],
                    res_s["stop_loss"],
                    res_s["tp1"],
                    res_s["tp2"],
                )
                logger.info(
                    f"{symbol} SHORT OK zone [{smx:.4f}-{smn:.4f}] "
                    f"entry={ep:.4f}, SL={sl:.4f}, TP1={tp1:.4f}, TP2={tp2:.4f}"
                )

                # 1) Anticipation SHORT
                if smx <= last_price <= smx*(1+ANTICIPATION_THRESHOLD):
                    if symbol not in sent_anticipation:
                        logger.info(f"{symbol} ⏳ anticipation SHORT à {last_price:.4f}")
                        await bot.send_message(
                            chat_id=os.environ["CHAT_ID"],
                            text=(
                                f"⏳ Anticipation SHORT {symbol}\n"
                                f"Zone {smx:.4f} → {smn:.4f}\n"
                                f"Prix actuel : {last_price:.4f}"
                            )
                        )
                        sent_anticipation.add(symbol)
                else:
                    sent_anticipation.discard(symbol)

                # 2) Zone SHORT atteinte
                if smx >= last_price >= smn:
                    if symbol not in sent_zone:
                        logger.info(f"{symbol} 🚨 zone SHORT atteinte à {last_price:.4f}")
                        await bot.send_message(
                            chat_id=os.environ["CHAT_ID"],
                            text=(
                                f"🚨 Zone SHORT atteinte {symbol}\n"
                                f"Entrée : {smx:.4f}–{smn:.4f}\n"
                                f"Prix actuel : {last_price:.4f}"
                            )
                        )
                        sent_zone.add(symbol)
                else:
                    sent_zone.discard(symbol)

                # 3) Signal final SHORT + graph
                if symbol not in sent_alert:
                    buf = generate_trade_graph(
                        symbol,
                        df,
                        {"entry": ep, "sl": sl, "tp": tp1,
                         "fvg_zone": (smx, smn)}
                    )
                    await bot.send_document(
                        chat_id=os.environ["CHAT_ID"],
                        document=InputFile(buf, filename="signal_short.png"),
                        caption=(
                            f"🔻 SHORT {symbol}\n"
                            f"Entry : {ep:.4f}\n"
                            f"SL    : {sl:.4f}\n"
                            f"TP1   : {tp1:.4f}\n"
                            f"TP2   : {tp2:.4f}"
                        )
                    )
                    sent_alert.add(symbol)
            else:
                sent_anticipation.discard(symbol)
                sent_zone.discard(symbol)
                sent_alert.discard(symbol)

        except Exception as e:
            logger.error(f"❌ Erreur sur {symbol} : {e}")

    # ─── Récapitulatif ───
    pct_l = accepted_long  / total * 100 if total else 0
    pct_s = accepted_short / total * 100 if total else 0
    logger.info(
        f"📊 Résumé scan : LONG {accepted_long}/{total} ({pct_l:.1f}%), "
        f"SHORT {accepted_short}/{total} ({pct_s:.1f}%)"
    )
