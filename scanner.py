# =====================================================================
# scanner.py — Bitget Desk Lead Scanner (Institutionnel H1+H4)
# Analyse structurelle + institutionnelle + momentum HTF
# Entrées LIMIT + SL/TP via BitgetTrader
# =====================================================================

import asyncio
import logging
import time
from datetime import datetime

from settings import (
    API_KEY, API_SECRET, API_PASSPHRASE,
    TELEGRAM_CHAT_ID, TELEGRAM_BOT_TOKEN,
    SCAN_INTERVAL_MIN,
)

from bitget_client import get_client
from bitget_trader import BitgetTrader
from analyze_signal import analyze_signal
from telegram.ext import Application

LOGGER = logging.getLogger(__name__)

# Cache macro pour éviter surcharge API
MACRO_CACHE = {
    "timestamp": 0,
    "BTC": None,
    "TOTAL": None,
    "TOTAL2": None,
}
MACRO_TTL = 90  # 90 sec


# =====================================================================
# TELEGRAM
# =====================================================================
async def send_telegram(text: str):
    try:
        app = Application.builder().token(TELEGRAM_BOT_TOKEN).build()
        async with app:
            await app.bot.send_message(chat_id=TELEGRAM_CHAT_ID, text=text)
    except Exception as e:
        LOGGER.error(f"Telegram send error: {e}")


# =====================================================================
# Récup dynamique des symboles Bitget USDT-M
# =====================================================================
async def fetch_all_symbols_bitget():
    client = await get_client(API_KEY, API_SECRET, API_PASSPHRASE)
    data = await client._request("GET", "/api/mix/v1/market/contracts", auth=False)

    syms = []
    for c in data.get("data", []):
        if c.get("quoteCoin") == "USDT":
            syms.append(c["symbol"])

    return syms


# =====================================================================
# Multi-timeframe (H1 + H4)
# =====================================================================
async def fetch_tf_ohlcv(symbol: str, tf: str, limit: int):
    client = await get_client(API_KEY, API_SECRET, API_PASSPHRASE)
    raw = await client.get_klines(symbol, granularity=tf, limit=limit)

    # convert into DataFrame-like format (dict of lists)
    ohlcv = {
        "timestamp": [],
        "open": [],
        "high": [],
        "low": [],
        "close": [],
        "volume": [],
    }

    try:
        for r in raw:
            ts, o, h, l, c, v = r
            ohlcv["timestamp"].append(float(ts))
            ohlcv["open"].append(float(o))
            ohlcv["high"].append(float(h))
            ohlcv["low"].append(float(l))
            ohlcv["close"].append(float(c))
            ohlcv["volume"].append(float(v))
    except:
        LOGGER.warning(f"Failed OHLCV format for {symbol}")

    return ohlcv


# =====================================================================
# Cache macro BTC / TOTAL / TOTAL2
# =====================================================================
async def fetch_macro_data():
    now = time.time()

    if now - MACRO_CACHE["timestamp"] < MACRO_TTL:
        return MACRO_CACHE

    client = await get_client(API_KEY, API_SECRET, API_PASSPHRASE)

    # BTCUSDT
    btc = await client.get_klines("BTCUSDT_UMCBL", "1H", limit=200)

    # TOTAL et TOTAL2 n’existent pas sur Bitget → fallback Binance ou CoinGecko ?
    # → Pour l’instant, neutre.
    total = None
    total2 = None

    MACRO_CACHE.update({
        "timestamp": now,
        "BTC": btc,
        "TOTAL": total,
        "TOTAL2": total2,
    })

    return MACRO_CACHE


# =====================================================================
# Analyse d’un symbole
# =====================================================================
async def process_symbol(symbol: str, trader: BitgetTrader):
    try:
        # Multi-timeframe
        h1 = await fetch_tf_ohlcv(symbol, "1H", 200)
        h4 = await fetch_tf_ohlcv(symbol, "4H", 200)

        if len(h1["close"]) < 50 or len(h4["close"]) < 50:
            return None

        macro = await fetch_macro_data()
        result = analyze_signal(symbol, h1, h4, macro)

        if not result or not result.get("signal"):
            return None

        sig = result["signal"]
        side = sig["side"]
        entry = sig["entry"]
        sl = sig["sl"]
        tp1 = sig.get("tp1")
        tp2 = sig.get("tp2")
        qty = sig["qty"]

        LOGGER.info(f"➡️ SIGNAL {symbol}: {side} @ {entry}")

        # Télégram
        await send_telegram(
            f"🚀 *Signal détecté*\n"
            f"• **{symbol}**\n"
            f"• Direction: *{side}*\n"
            f"• Entrée: `{entry}`\n"
            f"• SL: `{sl}`\n"
            f"• TP1: `{tp1}` | TP2: `{tp2}`\n"
            f"• Qty: `{qty}`\n"
            f"• Score: {result.get('score')}\n"
        )

        # Exécution LIMIT Bitget Desk Lead
        entry_res = await trader.place_limit(symbol, side, entry, qty)

        if not entry_res.get("ok"):
            LOGGER.error(f"Entry failed {symbol}: {entry_res}")
            return None

        # SL
        await trader.place_stop_loss(symbol, side, sl, qty)

        # TP1 / TP2
        if tp1:
            await trader.place_take_profit(symbol, side, tp1, qty * 0.5)
        if tp2:
            await trader.place_take_profit(symbol, side, tp2, qty * 0.5)

        return True

    except Exception as e:
        LOGGER.error(f"process_symbol error {symbol}: {e}")
        return None


# =====================================================================
# SCAN LOOP
# =====================================================================
async def scan_loop():
    trader = BitgetTrader(API_KEY, API_SECRET, API_PASSPHRASE)

    while True:
        try:
            LOGGER.info("==== SCAN START ====")

            symbols = await fetch_all_symbols_bitget()
            LOGGER.info(f"{len(symbols)} symbols chargés pour analyse.")

            tasks = [process_symbol(sym, trader) for sym in symbols]
            await asyncio.gather(*tasks)

            LOGGER.info("==== SCAN END ====")

        except Exception as e:
            LOGGER.error(f"Scan loop error: {e}")

        await asyncio.sleep(SCAN_INTERVAL_MIN * 60)


# =====================================================================
# MAIN
# =====================================================================
def start_scanner():
    asyncio.run(scan_loop())


if __name__ == "__main__":
    start_scanner()
