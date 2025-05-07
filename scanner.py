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
LOW_TF                 = "1hour"   # timeframe bas pour détection (1 h)
HIGH_TF                = "4hour"   # timeframe pour confirmation (4 h)
WINDOW                 = 20        # swing high/low sur WINDOW bougies de LOW_TF
ANTICIPATION_THRESHOLD = 0.003     # 0.3 %
IMB_THRESHOLD          = 0.2       # 20 % imbalance
RISK_PCT               = 0.01      # 1 % du capital

#── Seul le filtre Fibonacci/OTE est assoupli ──
FIBO_LOWER = 0.382  # 38,2 %
FIBO_UPPER = 0.886  # 88,6 %

# On conserve le buffer ATR existant
ATR_BUFFER = 0.5    # buffer = 0.5 ATR

logger = logging.getLogger(__name__)

# États anti-doublons
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
    logger.info(f"🔍 Démarrage du scan — {total} symbols (LOW_TF={LOW_TF})")

    # Compteurs de rejet par filtre
    cnt_len      = 0
    cnt_fibo_ote = 0
    cnt_trend    = 0
    cnt_rsmacd   = 0
    cnt_fvg      = 0

    # Compteurs de signaux acceptés
    accepted_l = 0
    accepted_s = 0

    for symbol in symbols:
        try:
            # 1) Bougies LOW_TF
            df_low = fetch_klines(symbol, interval=LOW_TF, limit=200)
            if len(df_low) < WINDOW:
                cnt_len += 1
                logger.info(f"{symbol} skip length ({len(df_low)}<{WINDOW})")
                continue
            last_price = df_low["close"].iat[-1]

            # 2) Swing high/low
            swing_high = df_low["high"].rolling(WINDOW).max().iat[-2]
            swing_low  = df_low["low"].rolling(WINDOW).min().iat[-2]

            # 3) Calcul ATR + buffer
            atr = compute_atr(df_low["high"], df_low["low"], df_low["close"], 14).iat[-1]
            tol = atr * ATR_BUFFER

            # 4) Fibonacci/OTE 38,2–88,6 % + buffer ATR
            fib_min = swing_low + FIBO_LOWER * (swing_high - swing_low)
            fib_max = swing_low + FIBO_UPPER * (swing_high - swing_low)
            if not (fib_min - tol <= last_price <= fib_max + tol):
                cnt_fibo_ote += 1
                logger.info(
                    f"{symbol} skip OTE: price={last_price:.4f} hors "
                    f"[{(fib_min - tol):.4f}-{(fib_max + tol):.4f}]"
                )
                continue

            # 5) Filtre de tendance (MA50 vs MA200)
            ma50  = df_low["close"].rolling(50).mean().iat[-1]
            ma200 = df_low["close"].rolling(200).mean().iat[-1]
            trend_long  = ma50 > ma200 and last_price > ma200
            trend_short = ma50 < ma200 and last_price < ma200
            if not (trend_long or trend_short):
                cnt_trend += 1
                logger.info(f"{symbol} skip trend: ma50={ma50:.4f}, ma200={ma200:.4f}")
                continue

            # 6) RSI & MACD (seuils existants)
            rsi = compute_rsi(df_low["close"], 14).iat[-1]
            macd_line, sig_line, _ = compute_macd(df_low["close"])
            macd_val = macd_line.iat[-1]
            sig_val  = sig_line.iat[-1]
            cond_long  = (rsi < 40 and macd_val > 0) or (rsi < 30 and macd_val > sig_val)
            cond_short = (rsi > 60 and macd_val < 0) or (rsi > 70 and macd_val < sig_val)
            if not (cond_long or cond_short):
                cnt_rsmacd += 1
                logger.info(
                    f"{symbol} skip RSI/MACD: RSI={rsi:.1f}, MACD={macd_val:.4f}"
                )
                continue

            # 7) Fair Value Gap
            if not (detect_fvg(df_low) or detect_fvg_short(df_low)):
                cnt_fvg += 1
                logger.info(f"{symbol} skip FVG")
                continue

            # 8) Orderbook imbalance (non bloquant)
            imb = get_orderbook_imbalance(symbol)
            logger.info(f"{symbol} orderbook imbalance: {imb}")

            # 9) Confirmation multi-TF (4 h)
            df_high = fetch_klines(symbol, interval=HIGH_TF, limit=50)
            logger.info(f"{symbol} → {len(df_high)} bougies {HIGH_TF} récupérées")
            confirmed = (
                analyze_market(symbol, df_high, side="long") or
                analyze_market(symbol, df_high, side="short")
            )
            if not confirmed:
                logger.info(f"{symbol} skip multi-TF ({HIGH_TF})")
                continue

            # 10) Calcul sizing (1 % de risque)
            bal      = get_account_balance(symbol)
            risk_amt = bal * RISK_PCT

            # ─── SCAN LONG ───
            res_l = analyze_market(symbol, df_low, side="long")
            if res_l and (imb is None or imb == "buy"):
                accepted_l += 1
                ep, sl, tp1, tp2 = (
                    res_l["entry_price"], res_l["stop_loss"],
                    res_l["tp1"], res_l["tp2"]
                )
                size = risk_amt / (ep - sl)
                # → envoi anticipation / zone / signal comme avant

            # ─── SCAN SHORT ───
            res_s = analyze_market(symbol, df_low, side="short")
            if res_s and (imb is None or imb == "sell"):
                accepted_s += 1
                # → idem pour le short

        except Exception as e:
            logger.error(f"❌ Erreur sur {symbol} : {e}")

    # ─── Récapitulatif par filtre ───
    logger.info("📊 **RÉCAPITULATIF FILTRAGE**")
    logger.info(f"• Total symbols    : {total}")
    logger.info(f"• Rejet length     : {cnt_len}      ({cnt_len/total*100:.1f}%)")
    logger.info(f"• Rejet OTE+tol    : {cnt_fibo_ote}  ({cnt_fibo_ote/total*100:.1f}%)")
    logger.info(f"• Rejet trend      : {cnt_trend}     ({cnt_trend/total*100:.1f}%)")
    logger.info(f"• Rejet RSI/MACD   : {cnt_rsmacd}   ({cnt_rsmacd/total*100:.1f}%)")
    logger.info(f"• Rejet FVG        : {cnt_fvg}       ({cnt_fvg/total*100:.1f}%)")
    logger.info(f"• LONGs acceptés   : {accepted_l}   ({accepted_l/total*100:.1f}%)")
    logger.info(f"• SHORTs acceptés  : {accepted_s}   ({accepted_s/total*100:.1f}%)")
