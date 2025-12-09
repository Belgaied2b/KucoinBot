# =====================================================================
# scanner.py — Bitget Desk Lead Scanner (Institutionnel H1+H4)
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
# TELEGRAM (instance unique)
# =====================================================================

TELEGRAM_APP: Application | None = None

async def init_telegram():
    """Initialise Telegram une seule fois."""
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

def to_df(raw):
    if not raw:
        return pd.DataFrame()
    df = pd.DataFrame(
        raw,
        columns=["time", "open", "high", "low", "close", "volume"]
    )
    df = df.astype(float)
    return df.sort_values("time").reset_index(drop=True)


# =====================================================================
# PROCESS SYMBOL
# =====================================================================

async def process_symbol(symbol: str, analyzer: SignalAnalyzer, trader: BitgetTrader, client):
    try:
        # Fetch H1 + H4 via Bitget v2
        df_h1 = await client.get_klines_df(symbol, "1H", 200)
        df_h4 = await client.get_klines_df(symbol, "4H", 200)

        if df_h1.empty or df_h4.empty or len(df_h1) < 80:
            return

        macro = {}  # plus tard : BTC / TOTAL…

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

        # Telegram
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
# MAIN SCAN LOOP
# =====================================================================

async def run_scanner():
    client = await get_client(API_KEY, API_SECRET, API_PASSPHRASE)
    trader = BitgetTrader(API_KEY, API_SECRET, API_PASSPHRASE)
    analyzer = SignalAnalyzer(API_KEY, API_SECRET, API_PASSPHRASE)

    while True:
        try:
            LOGGER.info("=== START SCAN ===")

            # NOUVEAU : récupération v2
            symbols = await client.get_contracts_list()

            if not symbols:
                LOGGER.warning("⚠️ Aucun symbole récupéré → arrêt temporaire")
                await asyncio.sleep(60)
                continue

            tasks = [
                process_symbol(sym, analyzer, trader, client)
                for sym in symbols
            ]
            await asyncio.gather(*tasks)

            LOGGER.info("=== END SCAN ===")

        except Exception as e:
            LOGGER.error(f"SCAN ERROR: {e}")

        await asyncio.sleep(SCAN_INTERVAL_MIN * 60)


# =====================================================================
# EXPORT POUR main.py
# =====================================================================

async def start_scanner():
    await run_scanner()


# =====================================================================
# MODE LOCAL
# =====================================================================

if __name__ == "__main__":
    try:
        asyncio.run(start_scanner())
    except RuntimeError:
        loop = asyncio.get_event_loop()
        loop.create_task(start_scanner())
        loop.run_forever()
