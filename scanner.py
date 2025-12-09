# =====================================================================
# scanner.py — Bitget Desk Lead Scanner (Institutionnel H1+H4)
# VERSION DEBUG — LOG COMPLET DES RÉPONSES BITGET
# =====================================================================

import asyncio
import logging
import time
import pandas as pd

from settings import (
    API_KEY, API_SECRET, API_PASSPHRASE,
    TELEGRAM_CHAT_ID, TELEGRAM_BOT_TOKEN,
    SCAN_INTERVAL_MIN,
)

from bitget_client import get_client
from bitget_trader import BitgetTrader
from analyze_signal import SignalAnalyzer
from telegram.ext import Application

LOGGER = logging.getLogger(__name__)


# =====================================================================
# TELEGRAM
# =====================================================================

TELEGRAM_APP: Application | None = None

async def init_telegram():
    global TELEGRAM_APP
    if TELEGRAM_APP is None:
        TELEGRAM_APP = Application.builder().token(TELEGRAM_BOT_TOKEN).build()
        await TELEGRAM_APP.initialize()
        await TELEGRAM_APP.start()

async def send_telegram(text: str):
    try:
        await init_telegram()
        await TELEGRAM_APP.bot.send_message(
            chat_id=TELEGRAM_CHAT_ID,
            text=text,
            parse_mode="Markdown"
        )
    except Exception as e:
        LOGGER.error(f"Telegram error: {e}")


# =====================================================================
# OHLCV → DataFrame
# =====================================================================

def to_df(ohlcv_raw):
    if not ohlcv_raw:
        return pd.DataFrame()

    df = pd.DataFrame(
        ohlcv_raw,
        columns=["timestamp", "open", "high", "low", "close", "volume"]
    )

    df = df.astype({
        "timestamp": "float",
        "open": "float",
        "high": "float",
        "low": "float",
        "close": "float",
        "volume": "float",
    })

    return df.sort_values("timestamp").reset_index(drop=True)


# =====================================================================
# FETCH SYMBOLS (VERSION DEBUG)
# =====================================================================

async def fetch_all_symbols_bitget(client):
    """
    DEBUG — Affiche la réponse brute de Bitget pour comprendre
    pourquoi la liste des contrats est vide.
    """

    r = await client._request(
        "GET",
        "/api/mix/v1/market/contracts",
        params={"productType": "umcbl"},
        auth=False
    )

    LOGGER.error(f"📡 RAW CONTRACTS RESPONSE: {r}")  # <<<<<<<<<<<<<< DEBUG CRUCIAL

    data = r.get("data")
    if not data:
        LOGGER.warning("⚠️ Bitget returned empty symbol list — fallback BTC/ETH")
        return ["BTCUSDT_UMCBL", "ETHUSDT_UMCBL"]

    syms = [c["symbol"] for c in data if c.get("symbol", "").endswith("_UMCBL")]

    if not syms:
        LOGGER.warning("⚠️ No UMCBL symbols detected — fallback")
        return ["BTCUSDT_UMCBL", "ETHUSDT_UMCBL"]

    return syms


# =====================================================================
# FETCH MULTI-TF OHLCV
# =====================================================================

async def fetch_tf_df(symbol: str, tf: str, limit: int = 200):
    client = await get_client(API_KEY, API_SECRET, API_PASSPHRASE)
    raw = await client.get_klines_df(symbol, tf, limit)
    return to_df(raw)


# =====================================================================
# MACRO CACHE
# =====================================================================

MACRO_CACHE = {"ts": 0, "BTC": None}
MACRO_TTL = 120

async def fetch_macro_data():
    now = time.time()
    if now - MACRO_CACHE["ts"] < MACRO_TTL:
        return MACRO_CACHE

    client = await get_client(API_KEY, API_SECRET, API_PASSPHRASE)
    raw_btc = await client.get_klines_df("BTCUSDT_UMCBL", "1H", 200)

    MACRO_CACHE.update({
        "ts": now,
        "BTC": to_df(raw_btc),
        "TOTAL": None,
        "TOTAL2": None,
    })

    return MACRO_CACHE


# =====================================================================
# PROCESS SYMBOL
# =====================================================================

async def process_symbol(symbol: str, analyzer: SignalAnalyzer, trader: BitgetTrader):
    try:
        df_h1 = await fetch_tf_df(symbol, "1H")
        df_h4 = await fetch_tf_df(symbol, "4H")

        if df_h1.empty or df_h4.empty or len(df_h1) < 80:
            LOGGER.error(f"[{symbol}] Not enough H1/H4 data")
            return

        macro = await fetch_macro_data()

        # Analyse principale
        result = await analyzer.analyze(symbol, df_h1, df_h4, macro)

        if not result or not result.get("signal"):
            return

        sig = result["signal"]

        side = sig["side"]
        entry = sig["entry"]
        sl = sig["sl"]
        tp1 = sig.get("tp1")
        tp2 = sig.get("tp2")
        qty = sig["qty"]

        LOGGER.warning(f"🎯 SIGNAL {symbol} → {side} @ {entry}")

        await send_telegram(
            f"🚀 *Signal détecté*\n"
            f"• **{symbol}**\n"
            f"• Direction: *{side}*\n"
            f"• Entrée: `{entry}`\n"
            f"• SL: `{sl}`\n"
            f"• TP1: `{tp1}` | TP2: `{tp2}`\n"
            f"• Qty: `{qty}`\n"
            f"• Score core: `{result.get('score')}`\n"
        )

        entry_res = await trader.place_limit(symbol, side, entry, qty)
        if entry_res.get("code") != "00000":
            LOGGER.error(f"Entry error {symbol}: {entry_res}")
            return

        await trader.place_stop_loss(symbol, side, sl, qty)

        if tp1:
            await trader.place_take_profit(symbol, side, tp1, qty * 0.5)
        if tp2:
            await trader.place_take_profit(symbol, side, tp2, qty * 0.5)

    except Exception as e:
        LOGGER.error(f"[{symbol}] process_symbol error: {e}")


# =====================================================================
# SCAN LOOP (ASYNC)
# =====================================================================

async def run_scanner():
    client = await get_client(API_KEY, API_SECRET, API_PASSPHRASE)
    trader = BitgetTrader(API_KEY, API_SECRET, API_PASSPHRASE)
    analyzer = SignalAnalyzer(API_KEY, API_SECRET, API_PASSPHRASE)

    while True:
        try:
            LOGGER.info("=== START SCAN ===")

            symbols = await fetch_all_symbols_bitget(client)  # << DEBUG VERSION
            tasks = [process_symbol(sym, analyzer, trader) for sym in symbols]

            await asyncio.gather(*tasks)

            LOGGER.info("=== END SCAN ===")

        except Exception as e:
            LOGGER.error(f"SCAN ERROR: {e}")

        await asyncio.sleep(SCAN_INTERVAL_MIN * 60)


# =====================================================================
# EXPORT — MAIN.PY UTILISE CE WRAPPER
# =====================================================================

async def start_scanner():
    await run_scanner()


if __name__ == "__main__":
    try:
        asyncio.run(start_scanner())
    except RuntimeError:
        loop = asyncio.get_event_loop()
        loop.create_task(start_scanner())
        loop.run_forever()
