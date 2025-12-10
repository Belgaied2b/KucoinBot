# =====================================================================
# scanner.py — Bitget Desk Lead Scanner (Institutionnel H1+H4)
# =====================================================================

import asyncio
import logging
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

# Combien de symboles on scanne par cycle
MAX_SYMBOLS_PER_SCAN = 80  # tu peux monter/descendre si besoin

# =====================================================================
# TELEGRAM (singleton)
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
# OHLCV normalisation
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
# PROCESSING SYMBOL
# =====================================================================

async def process_symbol(symbol: str, analyzer: SignalAnalyzer, trader: BitgetTrader, client):
    try:
        # Fetch TF
        df_h1 = await client.get_klines_df(symbol, "1H", 200)
        df_h4 = await client.get_klines_df(symbol, "4H", 200)

        if df_h1.empty or df_h4.empty or len(df_h1) < 80:
            return

        macro = {}  # futur BTC / TOTAL / etc.

        # === Analyse principale ===
        result = await analyzer.analyze(symbol, df_h1, df_h4, macro)

        if not result or not result.get("valid"):
            return

        side = result["side"]
        entry = result["entry"]
        sl = result["sl"]
        tp1 = result.get("tp1")
        tp2 = result.get("tp2")
        qty = result["qty"]

        LOGGER.warning(f"🎯 SIGNAL {symbol} → {side} @ {entry}")

        # === Telegram ===
        await send_telegram(
            f"🚀 *Signal détecté*\n"
            f"• **{symbol}**\n"
            f"• Direction: *{side}*\n"
            f"• Entrée: `{entry}`\n"
            f"• SL: `{sl}`\n"
            f"• TP1: `{tp1}` | TP2: `{tp2}`\n"
            f"• Qty: `{qty}`\n"
            f"• Inst Score: `{result.get('institutional_score')}`\n"
        )

        # === EXECUTION ===
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

    batch_start = 0  # index de départ dans la liste globale

    while True:
        try:
            LOGGER.info("=== START SCAN ===")

            symbols = await client.get_contracts_list()
            if not symbols:
                LOGGER.warning("⚠️ Aucun symbole récupéré → pause")
                await asyncio.sleep(60)
                continue

            total = len(symbols)

            # On boucle si on a atteint la fin
            if batch_start >= total:
                batch_start = 0

            batch_end = min(batch_start + MAX_SYMBOLS_PER_SCAN, total)
            batch = symbols[batch_start:batch_end]
            LOGGER.info(
                f"🔎 SCAN BATCH {batch_start}-{batch_end} / {total} (size={len(batch)})"
            )

            batch_start = batch_end  # prochain batch au scan suivant

            tasks = [
                process_symbol(sym, analyzer, trader, client)
                for sym in batch
            ]

            await asyncio.gather(*tasks)

            LOGGER.info("=== END SCAN ===")

        except Exception as e:
            LOGGER.error(f"SCAN ERROR: {e}")

        await asyncio.sleep(SCAN_INTERVAL_MIN * 60)


# =====================================================================
# EXPORT MAIN
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
