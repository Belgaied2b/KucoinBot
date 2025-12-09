# =====================================================================
# scanner.py — Desk Lead Scanner Bitget v2 (2025)
# =====================================================================

import asyncio
import logging
import time
import pandas as pd
from typing import List

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
# TELEGRAM (One global instance)
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
        LOGGER.error(f"Telegram Error: {e}")


# =====================================================================
# DF Formatting
# =====================================================================

def to_df(raw):
    if not raw:
        return pd.DataFrame()

    df = pd.DataFrame(raw, columns=["time", "open", "high", "low", "close", "volume"])
    df = df.astype({
        "time": float, "open": float, "high": float,
        "low": float, "close": float, "volume": float
    })
    return df.sort_values("time").reset_index(drop=True)


# =====================================================================
# SYMBOL FETCHING
# =====================================================================

async def fetch_all_symbols_bitget(client) -> List[str]:
    """
    Uses the NEW Bitget v2 contracts list.
    Only keeps symbols that are perpetual & trade in USDT.
    """
    contracts = await client.get_all_contracts()
    if not contracts:
        LOGGER.warning("⚠️ Bitget returned empty symbol list — fallback BTC/ETH")
        return ["BTCUSDT", "ETHUSDT"]

    syms = []
    for c in contracts:
        if c.get("quoteCoin") == "USDT" and c.get("symbolType") == "perpetual":
            syms.append(c["symbol"])

    return syms


# =====================================================================
# MULTI-TF OHLCV
# =====================================================================

async def fetch_tf_df(client, symbol: str, tf: str, limit=200):
    df = await client.get_klines_df(symbol, tf, limit)
    return df if isinstance(df, pd.DataFrame) else pd.DataFrame()


# =====================================================================
# MACRO CACHE
# =====================================================================

MACRO_CACHE = {"ts": 0, "BTC": None}
MACRO_TTL = 120

async def fetch_macro_data(client):
    now = time.time()
    if now - MACRO_CACHE["ts"] < MACRO_TTL:
        return MACRO_CACHE

    df_btc = await client.get_klines_df("BTCUSDT", "1H", 200)
    MACRO_CACHE.update({"ts": now, "BTC": df_btc})

    return MACRO_CACHE


# =====================================================================
# PROCESS ONE SYMBOL
# =====================================================================

async def process_symbol(symbol: str, client, analyzer: SignalAnalyzer, trader: BitgetTrader):
    try:
        df_h1 = await fetch_tf_df(client, symbol, "1H")
        df_h4 = await fetch_tf_df(client, symbol, "4H")

        if df_h1.empty or df_h4.empty or len(df_h1) < 80:
            return

        macro = await fetch_macro_data(client)

        # === Main Analyzer ===
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

        # Telegram alert
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

        # Execution
        entry_res = await trader.place_limit(symbol, side, entry, qty)
        if not entry_res.get("ok"):
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
# SCAN LOOP
# =====================================================================

async def run_scanner():
    client = await get_client(API_KEY, API_SECRET, API_PASSPHRASE)
    analyzer = SignalAnalyzer(API_KEY, API_SECRET, API_PASSPHRASE)
    trader = BitgetTrader(API_KEY, API_SECRET, API_PASSPHRASE)

    while True:
        try:
            LOGGER.info("=== START SCAN ===")

            symbols = await fetch_all_symbols_bitget(client)
            tasks = [process_symbol(sym, client, analyzer, trader) for sym in symbols]

            await asyncio.gather(*tasks)

            LOGGER.info("=== END SCAN ===")
        except Exception as e:
            LOGGER.error(f"SCAN ERROR: {e}")

        await asyncio.sleep(SCAN_INTERVAL_MIN * 60)


# =====================================================================
# EXPORT FOR main.py
# =====================================================================

async def start_scanner():
    await run_scanner()


if __name__ == "__main__":
    asyncio.run(start_scanner())
