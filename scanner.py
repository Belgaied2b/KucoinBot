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
            parse_mode="Markdown",
        )
    except Exception as e:
        LOGGER.error(f"Telegram error: {e}")


# =====================================================================
# OHLCV normalisation (pas vraiment utilisé ici, mais on garde)
# =====================================================================

def to_df(raw):
    if not raw:
        return pd.DataFrame()
    df = pd.DataFrame(
        raw,
        columns=["time", "open", "high", "low", "close", "volume"],
    )
    df = df.astype(float)
    return df.sort_values("time").reset_index(drop=True)


# =====================================================================
# PROCESSING SYMBOL
# =====================================================================

async def process_symbol(symbol: str, analyzer: SignalAnalyzer, trader: BitgetTrader, client):
    try:
        # Fetch TF H1 + H4 (séquentiel déjà à ce niveau)
        df_h1 = await client.get_klines_df(symbol, "1H", 200)
        df_h4 = await client.get_klines_df(symbol, "4H", 200)

        if df_h1.empty or df_h4.empty or len(df_h1) < 80:
            return

        macro = {}  # placeholder futur BTC / TOTAL etc.

        # === Analyse principale ===
        result = await analyzer.analyze(symbol, df_h1, df_h4, macro)

        # analyze_signal renvoie directement un dict de signal
        if not result or not result.get("valid"):
            return

        side = result["side"]          # "BUY" ou "SELL"
        entry = float(result["entry"])
        sl = float(result["sl"])
        tp1 = result.get("tp1")
        tp2 = result.get("tp2")
        qty = float(result["qty"])

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

        # === EXECUTION ENTRY ===
        entry_res = await trader.place_limit(symbol, side, entry, qty)
        if str(entry_res.get("code")) != "00000":
            LOGGER.error(f"Entry error {symbol}: {entry_res}")
            return

        # === STOP LOSS ===
        sl_res = await trader.place_stop_loss(symbol, side, sl, qty)
        if str(sl_res.get("code")) != "00000":
            LOGGER.error(f"SL error {symbol}: {sl_res}")

        # === TAKE PROFITS ===
        if tp1 is not None:
            tp1_res = await trader.place_take_profit(symbol, side, float(tp1), qty * 0.5)
            if str(tp1_res.get("code")) != "00000":
                LOGGER.error(f"TP1 error {symbol}: {tp1_res}")

        if tp2 is not None:
            tp2_res = await trader.place_take_profit(symbol, side, float(tp2), qty * 0.5)
            if str(tp2_res.get("code")) != "00000":
                LOGGER.error(f"TP2 error {symbol}: {tp2_res}")

    except Exception as e:
        LOGGER.error(f"[{symbol}] process_symbol error: {e}")


# =====================================================================
# MAIN SCAN LOOP — SÉQUENTIEL (pour respecter le rate limit)
# =====================================================================

async def run_scanner():
    client = await get_client(API_KEY, API_SECRET, API_PASSPHRASE)
    trader = BitgetTrader(API_KEY, API_SECRET, API_PASSPHRASE)
    analyzer = SignalAnalyzer(API_KEY, API_SECRET, API_PASSPHRASE)

    while True:
        start_time = time.time()
        try:
            LOGGER.info("=== START SCAN ===")

            symbols = await client.get_contracts_list()

            if not symbols:
                LOGGER.warning("⚠️ Aucun symbole récupéré → pause 60s")
                await asyncio.sleep(60)
                continue

            total = len(symbols)
            LOGGER.info(f"📊 Nombre de symboles à scanner : {total}")

            # SCAN SÉQUENTIEL
            for idx, sym in enumerate(symbols, start=1):
                LOGGER.debug(f"[SCAN {idx}/{total}] {sym}")
                await process_symbol(sym, analyzer, trader, client)

                # petit throttle pour ne pas saturer l'API
                await asyncio.sleep(0.05)

            LOGGER.info("=== END SCAN ===")

        except Exception as e:
            LOGGER.error(f"SCAN ERROR: {e}")

        # on respecte SCAN_INTERVAL_MIN entre 2 débuts de scan
        elapsed = time.time() - start_time
        wait = SCAN_INTERVAL_MIN * 60 - elapsed
        if wait > 0:
            await asyncio.sleep(wait)


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
