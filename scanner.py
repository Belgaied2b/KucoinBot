# scanner.py

import os
import logging
from telegram import InputFile

from kucoin_utils import get_kucoin_perps, fetch_klines
from signal_analysis  import analyze_market
from plot_signal      import generate_trade_graph
from indicators       import compute_rsi, compute_macd

# Seuil d’anticipation en % autour de l’entrée
ANTICIPATION_THRESHOLD = 0.003

logger = logging.getLogger(__name__)

# Pour éviter de renvoyer plusieurs fois la même alerte
sent_anticipation = set()
sent_zone        = set()
sent_alert       = set()

async def scan_and_send_signals(bot):
    symbols = get_kucoin_perps()
    total   = len(symbols)
    logger.info(f"🔍 Démarrage du scan — {total} contracts détectés")

    accepted_long  = 0
    accepted_short = 0

    for symbol in symbols:
        try:
            # ─── Récup OHLCV & indicateurs
            df         = fetch_klines(symbol)
            last_price = df["close"].iat[-1]
            rsi        = compute_rsi(df["close"], 14).iat[-1]
            macd_line, sig_line, _ = compute_macd(df["close"])
            macd_val, sig_val     = macd_line.iat[-1], sig_line.iat[-1]

            logger.info(
                f"{symbol} → prix={last_price:.4f}, RSI={rsi:.2f}, "
                f"MACD={macd_val:.6f}, SIG={sig_val:.6f}"
            )

            # ─── SCAN LONG ───
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
                    f"{symbol} LONG OK zone [{emn:.4f}-{emx:.4f}]  "
                    f"entry={ep:.4f}, SL={sl:.4f}, TP1={tp1:.4f}, TP2={tp2:.4f}"
                )

                # 1) Anticipation
                if emn*(1-ANTICIPATION_THRESHOLD) <= last_price < emn:
                    logger.info(f"{symbol} ⏳ anticipation LONG à {last_price:.4f}")
                    if symbol not in sent_anticipation:
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

                # 2) Zone atteinte
                if emn <= last_price <= emx:
                    logger.info(f"{symbol} 🚨 zone LONG atteinte à {last_price:.4f}")
                    if symbol not in sent_zone:
                        await bot.send_message(
                            chat_id=os.environ["CHAT_ID"],
                            text=(
                                f"🚨 Zone LONG atteinte {symbol}\n"
                                f"Entrée possible : {emn:.4f}–{emx:.4f}\n"
                                f"Prix actuel : {last_price:.4f}"
                            )
                        )
                        sent_zone.add(symbol)
                else:
                    sent_zone.discard(symbol)

                # 3) Signal final + graph
                if symbol not in sent_alert:
                    buf = generate_trade_graph(symbol, df, {
                        "entry": ep, "sl": sl, "tp": tp1,
                        "fvg_zone": (res_l["entry_min"], res_l["entry_max"])
                    })
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
                # on réinitialise les états si plus de signal
                sent_anticipation.discard(symbol)
                sent_zone.discard(symbol)
                sent_alert.discard(symbol)

            # ─── SCAN SHORT ───
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
                    f"{symbol} SHORT OK zone [{smx:.4f}-{smn:.4f}]  "
                    f"entry={ep:.4f}, SL={sl:.4f}, TP1={tp1:.4f}, TP2={tp2:.4f}"
                )

                # 1) Anticipation SHORT
                if smx <= last_price <= smx*(1+ANTICIPATION_THRESHOLD):
                    logger.info(f"{symbol} ⏳ anticipation SHORT à {last_price:.4f}")
                    key = f"short-anticip-{symbol}"
                    if symbol not in sent_anticipation:
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
                    logger.info(f"{symbol} 🚨 zone SHORT atteinte à {last_price:.4f}")
                    if symbol not in sent_zone:
                        await bot.send_message(
                            chat_id=os.environ["CHAT_ID"],
                            text=(
                                f"🚨 Zone SHORT atteinte {symbol}\n"
                                f"Entrée possible : {smx:.4f}–{smn:.4f}\n"
                                f"Prix actuel : {last_price:.4f}"
                            )
                        )
                        sent_zone.add(symbol)
                else:
                    sent_zone.discard(symbol)

                # 3) Signal final + graph
                if symbol not in sent_alert:
                    buf = generate_trade_graph(symbol, df, {
                        "entry": ep, "sl": sl, "tp": tp1,
                        "fvg_zone": (res_s["entry_max"], res_s["entry_min"])
                    })
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
    pct_long  = accepted_long  / total * 100 if total else 0
    pct_short = accepted_short / total * 100 if total else 0
    logger.info(
        "📊 Résumé scan : "
        f"LONGs acceptés {accepted_long}/{total} ({pct_long:.1f}%), "
        f"SHORTs acceptés {accepted_short}/{total} ({pct_short:.1f}%)"
    )
