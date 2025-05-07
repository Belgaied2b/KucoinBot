# scanner.py

import os
import logging
import httpx

from telegram import InputFile
from kucoin_utils    import BASE_URL, get_kucoin_perps, fetch_klines, get_account_balance
from signal_analysis import analyze_market
from plot_signal     import generate_trade_graph
from indicators      import compute_rsi, compute_macd, compute_atr
from signal_analysis import detect_fvg, detect_fvg_short

# ─────────── Paramètres ───────────
LOW_TF                 = "1hour"    # timeframe bas pour détection
HIGH_TF                = "4hour"    # timeframe pour confirmation
WINDOW                 = 20         # swing high/low sur WINDOW bougies
ANTICIPATION_THRESHOLD = 0.003      # 0.3 %
IMB_THRESHOLD          = 0.2        # 20 % imbalance non bloquant
RISK_PCT               = 0.01       # 1 % du capital

#── Seuils FibO élargi + buffer ATR ──
FIBO_LOWER = 0.382   # 38,2 %
FIBO_UPPER = 0.886   # 88,6 %
ATR_BUFFER = 0.5     # 0.5 ATR

#── Seuils RSI & MACD ──
RSI_LONG   = 45      # RSI < 45 pour signal long
RSI_SHORT  = 55      # RSI > 55 pour signal short

logger = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO)

# États anti-doublons
sent_anticipation = set()
sent_zone         = set()
sent_alert        = set()

def get_orderbook_imbalance(symbol: str) -> str | None:
    """Orderbook snapshot KuCoin Futures (non bloquant)."""
    try:
        url  = f"{BASE_URL}/api/v1/level2/snapshot"
        resp = httpx.get(url, params={"symbol": symbol, "limit": 20}, timeout=5)
        resp.raise_for_status()
        data    = resp.json().get("data", {})
        bids    = data.get("bids", [])[:10]
        asks    = data.get("asks", [])[:10]
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
    logger.info(f"🔍 Démarrage du scan — {total} symbols (LOW_TF={LOW_TF})")

    # Compteurs de rejet
    cnt_len      = cnt_fibo_ote = cnt_trend = cnt_rsmacd = cnt_fvg = 0
    # Compteurs d’acceptation
    accepted_l = accepted_s = 0

    for symbol in symbols:
        try:
            # 1) Bougies LOW_TF
            df_low = fetch_klines(symbol, interval=LOW_TF, limit=200)
            if len(df_low) < WINDOW:
                cnt_len += 1
                logger.info(f"{symbol}: skip length ({len(df_low)}<{WINDOW})")
                continue
            price = df_low["close"].iat[-1]

            # 2) Swing high/low
            swing_high = df_low["high"].rolling(WINDOW).max().iat[-2]
            swing_low  = df_low["low"].rolling(WINDOW).min().iat[-2]

            # 3) ATR & tolérance
            atr = compute_atr(df_low["high"], df_low["low"], df_low["close"], 14).iat[-1]
            tol = atr * ATR_BUFFER

            # 4) FibO/OTE élargi + buffer ATR
            fib_min = swing_low + FIBO_LOWER * (swing_high - swing_low)
            fib_max = swing_low + FIBO_UPPER * (swing_high - swing_low)
            if not (fib_min - tol <= price <= fib_max + tol):
                cnt_fibo_ote += 1
                logger.info(
                    f"{symbol}: skip OTE (price={price:.4f} hors "
                    f"[{(fib_min - tol):.4f}-{(fib_max + tol):.4f}])"
                )
                continue

            # 5) Trend filter (MA50 vs MA200)
            ma50  = df_low["close"].rolling(50).mean().iat[-1]
            ma200 = df_low["close"].rolling(200).mean().iat[-1]
            ok_trend_long  = ma50 > ma200 and price > ma200
            ok_trend_short = ma50 < ma200 and price < ma200
            if not (ok_trend_long or ok_trend_short):
                cnt_trend += 1
                logger.info(f"{symbol}: skip trend (ma50={ma50:.4f}, ma200={ma200:.4f})")
                continue

            # 6) RSI & MACD
            rsi = compute_rsi(df_low["close"], 14).iat[-1]
            macd_line, sig_line, _ = compute_macd(df_low["close"])
            macd_val = macd_line.iat[-1]
            sig_val  = sig_line.iat[-1]
            ok_long  = rsi < RSI_LONG and macd_val > sig_val
            ok_short = rsi > RSI_SHORT and macd_val < sig_val
            if not (ok_long or ok_short):
                cnt_rsmacd += 1
                logger.info(
                    f"{symbol}: skip RSI/MACD (RSI={rsi:.1f}, MACD={macd_val:.4f}, SIG={sig_val:.4f})"
                )
                continue

            # 7) Fair Value Gap
            has_fvg_long  = detect_fvg(df_low)
            has_fvg_short = detect_fvg_short(df_low)
            if not (has_fvg_long or has_fvg_short):
                cnt_fvg += 1
                logger.info(f"{symbol}: skip FVG")
                continue

            # 8) Orderbook (non bloquant)
            imb = get_orderbook_imbalance(symbol)
            logger.info(f"{symbol}: orderbook imbalance = {imb}")

            # 9) Confirmation multi-TF (4 h)
            df_high = fetch_klines(symbol, interval=HIGH_TF, limit=50)
            logger.info(f"{symbol}: {len(df_high)} bougies {HIGH_TF} récupérées")
            confirmed = (
                analyze_market(symbol, df_high, side="long")
                or analyze_market(symbol, df_high, side="short")
            )
            if not confirmed:
                logger.info(f"{symbol}: skip multi-TF (pas de signal sur {HIGH_TF})")
                continue

            # 10) Sizing & envoi de signal
            bal      = get_account_balance(symbol)
            risk_amt = bal * RISK_PCT

            # Analyse finale sur LOW_TF
            res = analyze_market(symbol, df_low, side="long") \
                  or analyze_market(symbol, df_low, side="short")
            if not res:
                # (au cas où)
                continue

            ep, sl, tp1, tp2 = (
                res["entry_price"], res["stop_loss"],
                res["tp1"], res["tp2"]
            )
            size = risk_amt / abs(ep - sl)

            # Anticipation
            if symbol not in sent_anticipation:
                buf = generate_trade_graph(symbol, df_low, {
                    "entry": ep, "sl": sl, "tp": tp1,
                    "fvg_zone": (res["entry_min"], res["entry_max"])
                })
                await bot.send_photo(
                    chat_id=os.environ["CHAT_ID"],
                    photo=InputFile(buf, f"{symbol}.png"),
                    caption=(
                        f"⏳ Anticipation "
                        f"{'LONG' if ok_long else 'SHORT'} {symbol}\n"
                        f"Zone {res['entry_min']:.4f}→{res['entry_max']:.4f}"
                    )
                )
                sent_anticipation.add(symbol)

            # Zone atteinte
            if (symbol not in sent_zone
               and res["entry_min"] <= price <= res["entry_max"]):
                await bot.send_message(
                    chat_id=os.environ["CHAT_ID"],
                    text=(
                        f"🚨 Zone atteinte "
                        f"{'LONG' if ok_long else 'SHORT'} {symbol}\n"
                        f"Prix {price:.4f}"
                    )
                )
                sent_zone.add(symbol)

            # Signal final
            if symbol not in sent_alert:
                buf = generate_trade_graph(symbol, df_low, {
                    "entry": ep, "sl": sl, "tp": tp1,
                    "fvg_zone": (res["entry_min"], res["entry_max"])
                })
                await bot.send_document(
                    chat_id=os.environ["CHAT_ID"],
                    document=InputFile(buf, filename="signal.png"),
                    caption=(
                        f"{'🟢 LONG' if ok_long else '🔻 SHORT'} {symbol}\n"
                        f"Entry : {ep:.4f}\n"
                        f"SL    : {sl:.4f}\n"
                        f"TP1   : {tp1:.4f}\n"
                        f"TP2   : {tp2:.4f}\n"
                        f"Taille : {size:.4f}"
                    )
                )
                sent_alert.add(symbol)
                if ok_long:
                    accepted_l += 1
                else:
                    accepted_s += 1

        except Exception as e:
            logger.error(f"{symbol} ❌ {e}")

    # ─── Récapitulatif ───────────
    logger.info("📊 **RÉCAPITULATIF FILTRAGE**")
    logger.info(f"• Total symbols    : {total}")
    logger.info(f"• Rejet length     : {cnt_len}      ({cnt_len/total*100:.1f}%)")
    logger.info(f"• Rejet OTE+tol    : {cnt_fibo_ote}  ({cnt_fibo_ote/total*100:.1f}%)")
    logger.info(f"• Rejet trend      : {cnt_trend}     ({cnt_trend/total*100:.1f}%)")
    logger.info(f"• Rejet RSI/MACD   : {cnt_rsmacd}   ({cnt_rsmacd/total*100:.1f}%)")
    logger.info(f"• Rejet FVG        : {cnt_fvg}       ({cnt_fvg/total*100:.1f}%)")
    logger.info(f"• LONGs acceptés   : {accepted_l}   ({accepted_l/total*100:.1f}%)")
    logger.info(f"• SHORTs acceptés  : {accepted_s}  ({accepted_s/total*100:.1f}%)")
