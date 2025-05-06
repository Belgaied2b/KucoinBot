# scanner.py

import os
import logging
import httpx

from telegram import InputFile
from kucoin_utils    import BASE_URL, get_kucoin_perps, fetch_klines, get_account_balance
from signal_analysis import detect_fvg, detect_fvg_short, analyze_market
from plot_signal     import generate_trade_graph
from indicators      import compute_rsi, compute_macd, compute_atr

# ─────────── Paramètres ───────────
LOW_TF                 = "15min"   # timeframe bas
HIGH_TF                = "4hour"   # confirmation multi‐TF
WINDOW                 = 20        # swing high/low sur 20 périodes de LOW_TF
ANTICIPATION_THRESHOLD = 0.003     # 0.3%
IMB_THRESHOLD          = 0.2       # 20% imbalance
RISK_PCT               = 0.01      # 1% du capital

logger = logging.getLogger(__name__)

# États anti‐doublons
sent_anticipation = set()
sent_zone         = set()
sent_alert        = set()

def get_orderbook_imbalance(symbol: str) -> str | None:
    """Snapshot Level2 KuCoin Futures."""
    try:
        url  = f"{BASE_URL}/api/v1/level2/snapshot"
        resp = httpx.get(url, params={"symbol": symbol, "limit": 20}, timeout=5)
        resp.raise_for_status()
        data = resp.json().get("data", {})
        bids = data.get("bids", [])[:10]
        asks = data.get("asks", [])[:10]
        bid_vol = sum(float(b[1]) for b in bids)
        ask_vol = sum(float(a[1]) for a in asks)
        if bid_vol > ask_vol * (1 + IMB_THRESHOLD):
            return "buy"
        if ask_vol > bid_vol * (1 + IMB_THRESHOLD):
            return "sell"
    except Exception as e:
        logger.warning(f"{symbol} orderbook error (filtre désactivé): {e}")
    return None

async def scan_and_send_signals(bot):
    symbols = get_kucoin_perps()
    total   = len(symbols)
    logger.info(f"🔍 Démarrage du scan — {total} contracts détectés")

    # Compteurs de rejet par filtre
    cnt_len      = 0  # trop peu de données
    cnt_fibo_ote = 0  # hors zone Fibonacci/OTE
    cnt_trend    = 0  # tendance MM50/MM200
    cnt_rsmacd   = 0  # RSI & MACD
    cnt_fvg      = 0  # absence de Fair Value Gap

    # Compteurs d’acceptation
    accepted_l = 0
    accepted_s = 0

    for symbol in symbols:
        try:
            # 1) Récupère les bougies en LOW_TF
            df_low = fetch_klines(symbol, interval=LOW_TF, limit=200)
            if len(df_low) < WINDOW:
                cnt_len += 1
                logger.info(f"{symbol} skip length ({len(df_low)}<{WINDOW})")
                continue

            last_price = df_low["close"].iat[-1]

            # 2) Swing high/low
            swing_high = df_low["high"].rolling(WINDOW).max().iat[-2]
            swing_low  = df_low["low"].rolling(WINDOW).min().iat[-2]

            # 3) Fibonacci zone (OTE)
            fib_min = swing_low + 0.618 * (swing_high - swing_low)
            fib_max = swing_low + 0.786 * (swing_high - swing_low)
            if not (fib_min <= last_price <= fib_max):
                cnt_fibo_ote += 1
                logger.info(
                    f"{symbol} skip Fibo/OTE: price={last_price:.4f} hors "
                    f"[{fib_min:.4f}-{fib_max:.4f}]"
                )
                continue

            # 4) Trend filter (MA50 vs MA200)
            ma50  = df_low["close"].rolling(50).mean().iat[-1]
            ma200 = df_low["close"].rolling(200).mean().iat[-1]
            trend_long  = ma50 > ma200 and last_price > ma200
            trend_short = ma50 < ma200 and last_price < ma200
            if not (trend_long or trend_short):
                cnt_trend += 1
                logger.info(
                    f"{symbol} skip trend: ma50={ma50:.4f}, ma200={ma200:.4f}"
                )
                continue

            # 5) RSI & MACD
            rsi = compute_rsi(df_low["close"], 14).iat[-1]
            macd_line, sig_line, _ = compute_macd(df_low["close"])
            macd_val = macd_line.iat[-1]
            sig_val  = sig_line.iat[-1]
            cond_long  = rsi < 30 and macd_val > sig_val
            cond_short = rsi > 70 and macd_val < sig_val
            if not (cond_long or cond_short):
                cnt_rsmacd += 1
                logger.info(
                    f"{symbol} skip RSI/MACD: RSI={rsi:.1f}, "
                    f"MACD={macd_val:.4f}, SIG={sig_val:.4f}"
                )
                continue

            # 6) Fair Value Gap
            has_fvg_long  = detect_fvg(df_low)
            has_fvg_short = detect_fvg_short(df_low)
            if not (has_fvg_long or has_fvg_short):
                cnt_fvg += 1
                logger.info(f"{symbol} skip FVG")
                continue

            # 7) Orderbook imbalance (non bloquant)
            imb = get_orderbook_imbalance(symbol)
            logger.info(f"{symbol} orderbook imbalance: {imb}")

            # 8) Confirmation multi‐TF (HIGH_TF)
            df_high = fetch_klines(symbol, interval=HIGH_TF, limit=50)
            high_signal = (
                analyze_market(symbol, df_high, side="long") or
                analyze_market(symbol, df_high, side="short")
            )
            if not high_signal:
                logger.info(
                    f"{symbol} skip multi‐TF (pas de signal sur {HIGH_TF})"
                )
                continue

            # 9) Calcul ATR & sizing
            atr      = compute_atr(df_low["high"], df_low["low"],
                                   df_low["close"], 14).iat[-1]
            bal      = get_account_balance(symbol)
            risk_amt = bal * RISK_PCT
            logger.info(
                f"{symbol} ATR={atr:.4f}, capital={bal:.2f}, risk_amt={risk_amt:.2f}"
            )

            # ─── SCAN LONG ───
            res_l = analyze_market(symbol, df_low, side="long")
            if res_l and (imb is None or imb == "buy"):
                accepted_l += 1
                ep, sl, tp1, tp2 = (
                    res_l["entry_price"], res_l["stop_loss"],
                    res_l["tp1"], res_l["tp2"]
                )
                size = risk_amt / (ep - sl)

                # Anticipation
                if fib_min * (1 - ANTICIPATION_THRESHOLD) <= last_price < fib_min:
                    if symbol not in sent_anticipation:
                        await bot.send_message(
                            chat_id=os.environ["CHAT_ID"],
                            text=(
                                f"⏳ Anticipation LONG {symbol}\n"
                                f"Zone {fib_min:.4f} → {fib_max:.4f}\n"
                                f"Prix actuel : {last_price:.4f}"
                            )
                        )
                        sent_anticipation.add(symbol)
                else:
                    sent_anticipation.discard(symbol)

                # Zone atteinte
                if fib_min <= last_price <= fib_max:
                    if symbol not in sent_zone:
                        await bot.send_message(
                            chat_id=os.environ["CHAT_ID"],
                            text=(
                                f"🚨 Zone LONG atteinte {symbol}\n"
                                f"Entrée : {fib_min:.4f}–{fib_max:.4f}\n"
                                f"Prix actuel : {last_price:.4f}"
                            )
                        )
                        sent_zone.add(symbol)
                else:
                    sent_zone.discard(symbol)

                # Signal final + graph
                if symbol not in sent_alert:
                    buf = generate_trade_graph(
                        symbol, df_low,
                        {"entry": ep, "sl": sl, "tp": tp1,
                         "fvg_zone": (res_l["entry_min"], res_l["entry_max"])}
                    )
                    await bot.send_document(
                        chat_id=os.environ["CHAT_ID"],
                        document=InputFile(buf, filename="signal_long.png"),
                        caption=(
                            f"🟢 LONG {symbol}\n"
                            f"Entry : {ep:.4f}\n"
                            f"SL    : {sl:.4f}\n"
                            f"TP1   : {tp1:.4f}\n"
                            f"TP2   : {tp2:.4f}\n"
                            f"Taille: {size:.4f}"
                        )
                    )
                    sent_alert.add(symbol)
            else:
                sent_anticipation.discard(symbol)
                sent_zone.discard(symbol)
                sent_alert.discard(symbol)

            # ─── SCAN SHORT ───
            res_s = analyze_market(symbol, df_low, side="short")
            if res_s and (imb is None or imb == "sell"):
                accepted_s += 1
                ep, sl, tp1, tp2 = (
                    res_s["entry_price"], res_s["stop_loss"],
                    res_s["tp1"], res_s["tp2"]
                )
                size = risk_amt / (sl - ep)

                # Anticipation
                if fib_max <= last_price <= fib_max * (1 + ANTICIPATION_THRESHOLD):
                    if symbol not in sent_anticipation:
                        await bot.send_message(
                            chat_id=os.environ["CHAT_ID"],
                            text=(
                                f"⏳ Anticipation SHORT {symbol}\n"
                                f"Zone {fib_max:.4f} → {fib_min:.4f}\n"
                                f"Prix actuel : {last_price:.4f}"
                            )
                        )
                        sent_anticipation.add(symbol)
                else:
                    sent_anticipation.discard(symbol)

                # Zone atteinte
                if fib_max >= last_price >= fib_min:
                    if symbol not in sent_zone:
                        await bot.send_message(
                            chat_id=os.environ["CHAT_ID"],
                            text=(
                                f"🚨 Zone SHORT atteinte {symbol}\n"
                                f"Entrée : {fib_max:.4f}–{fib_min:.4f}\n"
                                f"Prix actuel : {last_price:.4f}"
                            )
                        )
                        sent_zone.add(symbol)
                else:
                    sent_zone.discard(symbol)

                # Signal final + graph
                if symbol not in sent_alert:
                    buf = generate_trade_graph(
                        symbol, df_low,
                        {"entry": res_s["entry_price"], "sl": sl, "tp": tp1,
                         "fvg_zone": (res_s["entry_max"], res_s["entry_min"])}
                    )
                    await bot.send_document(
                        chat_id=os.environ["CHAT_ID"],
                        document=InputFile(buf, filename="signal_short.png"),
                        caption=(
                            f"🔻 SHORT {symbol}\n"
                            f"Entry : {ep:.4f}\n"
                            f"SL    : {sl:.4f}\n"
                            f"TP1   : {tp1:.4f}\n"
                            f"TP2   : {tp2:.4f}\n"
                            f"Taille: {size:.4f}"
                        )
                    )
                    sent_alert.add(symbol)
            else:
                sent_anticipation.discard(symbol)
                sent_zone.discard(symbol)
                sent_alert.discard(symbol)

        except Exception as e:
            logger.error(f"❌ Erreur sur {symbol} : {e}")

    # ─── Récapitulatif par filtre ───────────
    logger.info("📊 **RÉCAPITULATIF FILTRAGE**")
    logger.info(f"• Total symbols      : {total}")
    logger.info(f"• Rejet longueur      : {cnt_len} ({cnt_len/total*100:.1f}%)")
    logger.info(f"• Rejet Fibo/OTE      : {cnt_fibo_ote} ({cnt_fibo_ote/total*100:.1f}%)")
    logger.info(f"• Rejet trend         : {cnt_trend} ({cnt_trend/total*100:.1f}%)")
    logger.info(f"• Rejet RSI/MACD      : {cnt_rsmacd} ({cnt_rsmacd/total*100:.1f}%)")
    logger.info(f"• Rejet FVG           : {cnt_fvg} ({cnt_fvg/total*100:.1f}%)")
    logger.info(f"• LONGs acceptés      : {accepted_l} ({accepted_l/total*100:.1f}%)")
    logger.info(f"• SHORTs acceptés     : {accepted_s} ({accepted_s/total*100:.1f}%)")
