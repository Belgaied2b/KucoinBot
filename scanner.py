# scanner.py

import os
import logging
import httpx

from telegram import InputFile
from kucoin_utils   import BASE_URL, get_kucoin_perps, fetch_klines, get_account_balance
from signal_analysis import analyze_market
from plot_signal     import generate_trade_graph
from indicators      import compute_rsi, compute_macd, compute_atr

# Seuil d’anticipation en % autour de l’entrée
ANTICIPATION_THRESHOLD = 0.003
# Seuil d’imbalance pour orderbook (20% de différence)
IMB_THRESHOLD         = 0.2
# Confirmation multi‐TF : intervalle haut pour confirmer (ici "4hour")
HIGH_TF               = "4hour"

logger = logging.getLogger(__name__)

sent_anticipation = set()
sent_zone        = set()
sent_alert       = set()

def get_orderbook_imbalance(symbol: str) -> str | None:
    """
    Récupère le snapshot Level2 KuCoin et renvoie 'buy' si bids > asks*(1+th),
    'sell' si asks > bids*(1+th), sinon None.
    """
    url = f"{BASE_URL}/api/v1/level2/depth"
    resp = httpx.get(url, params={"symbol": symbol, "limit": 20})
    resp.raise_for_status()
    data = resp.json().get("data", {})
    bids = data.get("bids", [])
    asks = data.get("asks", [])
    bid_vol = sum(float(b[1]) for b in bids[:10])
    ask_vol = sum(float(a[1]) for a in asks[:10])
    if bid_vol > ask_vol * (1 + IMB_THRESHOLD):
        return "buy"
    if ask_vol > bid_vol * (1 + IMB_THRESHOLD):
        return "sell"
    return None

async def scan_and_send_signals(bot):
    symbols = get_kucoin_perps()
    total   = len(symbols)
    logger.info(f"🔍 Démarrage du scan — {total} contracts détectés")

    accepted_long  = 0
    accepted_short = 0

    for symbol in symbols:
        try:
            # ─── 1) Récup OHLCV minute + indicateurs ───
            df_low     = fetch_klines(symbol, interval="1min", limit=200)
            last_price = df_low["close"].iat[-1]
            rsi        = compute_rsi(df_low["close"], 14).iat[-1]
            macd_line, sig_line, _ = compute_macd(df_low["close"])
            macd_val, sig_val     = macd_line.iat[-1], sig_line.iat[-1]

            logger.info(
                f"{symbol} → price={last_price:.4f}, RSI={rsi:.2f}, "
                f"MACD={macd_val:.6f}, SIG={sig_val:.6f}"
            )

            # ─── 2) Filtre Orderbook / Imbalance ───
            imb = get_orderbook_imbalance(symbol)
            logger.info(f"{symbol} orderbook imbalance: {imb}")
            if imb is None:
                logger.info(f"{symbol} skip (pas d’imbalance assez forte)")
                continue

            # ─── 3) Confirmation multi‐TF ───
            df_high = fetch_klines(symbol, interval=HIGH_TF, limit=50)
            if not analyze_market(symbol, df_high, side="long") and not analyze_market(symbol, df_high, side="short"):
                logger.info(f"{symbol} skip (pas de signal sur {HIGH_TF})")
                continue

            # ─── 4) Calcul ATR + taille de position ───
            atr = compute_atr(df_low["high"], df_low["low"], df_low["close"], 14).iat[-1]
            bal = get_account_balance(symbol)
            risk_amt = bal * 0.01  # 1% du capital
            # (Taille sera recalculée par position)
            logger.info(f"{symbol} ATR={atr:.4f}, capital={bal:.2f}, risk_amt={risk_amt:.2f}")

            # ─── 5) Test LONG ───
            res_l = analyze_market(symbol, df_low, side="long")
            if res_l and imb == "buy":
                accepted_long += 1
                emn, emx = res_l["entry_min"], res_l["entry_max"]
                ep, sl, tp1, tp2 = (
                    res_l["entry_price"],
                    res_l["stop_loss"],
                    res_l["tp1"],
                    res_l["tp2"],
                )
                # calc size = risk_amt / (entry - sl)
                size = risk_amt / (ep - sl)
                logger.info(
                    f"{symbol} LONG OK zone [{emn:.4f}-{emx:.4f}] entry={ep:.4f}, "
                    f"SL={sl:.4f}, TP1={tp1:.4f}, TP2={tp2:.4f}, size={size:.4f}"
                )

                # 5.1) Anticipation LONG
                if emn * (1 - ANTICIPATION_THRESHOLD) <= last_price < emn:
                    if symbol not in sent_anticipation:
                        await bot.send_message(
                            chat_id=os.environ["CHAT_ID"],
                            text=(
                                f"⏳ Anticipation LONG {symbol}\n"
                                f"Zone {emn:.4f}→{emx:.4f}\n"
                                f"Prix {last_price:.4f}"
                            )
                        )
                        sent_anticipation.add(symbol)
                else:
                    sent_anticipation.discard(symbol)

                # 5.2) Zone LONG atteinte
                if emn <= last_price <= emx:
                    if symbol not in sent_zone:
                        await bot.send_message(
                            chat_id=os.environ["CHAT_ID"],
                            text=(
                                f"🚨 Zone LONG atteinte {symbol}\n"
                                f"Entrée : {emn:.4f}–{emx:.4f}\n"
                                f"Prix : {last_price:.4f}"
                            )
                        )
                        sent_zone.add(symbol)
                else:
                    sent_zone.discard(symbol)

                # 5.3) Signal final + graph
                if symbol not in sent_alert:
                    buf = generate_trade_graph(
                        symbol, df_low,
                        {"entry": ep, "sl": sl, "tp": tp1, "fvg_zone": (emn, emx)}
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

            # ─── 6) Test SHORT ───
            res_s = analyze_market(symbol, df_low, side="short")
            if res_s and imb == "sell":
                accepted_short += 1
                smx, smn = res_s["entry_max"], res_s["entry_min"]
                ep, sl, tp1, tp2 = (
                    res_s["entry_price"],
                    res_s["stop_loss"],
                    res_s["tp1"],
                    res_s["tp2"],
                )
                size = risk_amt / (sl - ep)
                logger.info(
                    f"{symbol} SHORT OK zone [{smx:.4f}-{smn:.4f}] entry={ep:.4f}, "
                    f"SL={sl:.4f}, TP1={tp1:.4f}, TP2={tp2:.4f}, size={size:.4f}"
                )

                # 6.1) Anticipation SHORT
                if smx <= last_price <= smx * (1 + ANTICIPATION_THRESHOLD):
                    if symbol not in sent_anticipation:
                        await bot.send_message(
                            chat_id=os.environ["CHAT_ID"],
                            text=(
                                f"⏳ Anticipation SHORT {symbol}\n"
                                f"Zone {smx:.4f}→{smn:.4f}\n"
                                f"Prix {last_price:.4f}"
                            )
                        )
                        sent_anticipation.add(symbol)
                else:
                    sent_anticipation.discard(symbol)

                # 6.2) Zone SHORT atteinte
                if smx >= last_price >= smn:
                    if symbol not in sent_zone:
                        await bot.send_message(
                            chat_id=os.environ["CHAT_ID"],
                            text=(
                                f"🚨 Zone SHORT atteinte {symbol}\n"
                                f"Entrée : {smx:.4f}–{smn:.4f}\n"
                                f"Prix : {last_price:.4f}"
                            )
                        )
                        sent_zone.add(symbol)
                else:
                    sent_zone.discard(symbol)

                # 6.3) Signal final + graph
                if symbol not in sent_alert:
                    buf = generate_trade_graph(
                        symbol, df_low,
                        {"entry": ep, "sl": sl, "tp": tp1, "fvg_zone": (smx, smn)}
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

    # ─── Récapitulatif ───
    pct_l = accepted_long  / total * 100 if total else 0
    pct_s = accepted_short / total * 100 if total else 0
    logger.info(
        f"📊 Résumé scan : LONGs {accepted_long}/{total} ({pct_l:.1f}%), "
        f"SHORTs {accepted_short}/{total} ({pct_s:.1f}%)"
    )
