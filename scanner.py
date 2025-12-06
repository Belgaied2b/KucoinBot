# =====================================================================
# scanner.py — Bitget Desk Lead Scanner (Institutionnel H1+H4)
# Version stable, corrigée, compatible SignalAnalyzer v1.0
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
# TELEGRAM — INSTANCE UNIQUE (fix erreurs "app already started")
# =====================================================================

TELEGRAM_APP: Application | None = None

async def init_telegram():
    """Initialize telegram application once."""
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
        raw, columns=["timestamp", "open", "high", "low", "close", "volume"]
    )

    df = df.astype({
        "timestamp": float,
        "open": float,
        "high": float,
        "low": float,
        "close": float,
        "volume": float,
    })

    return df.sort_values("timestamp").reset_index(drop=True)


# =====================================================================
# FETCH SYMBOLS
# =====================================================================

async def fetch_all_symbols_bitget(client):
    data = await client._request("GET", "/api/mix/v1/market/contracts", auth=False)

    symbols = []
    for c in data.get("data", []):
        if c.get("quoteCoin") == "USDT" and c["symbol"].endswith("_UMCBL"):
            symbols.append(c["symbol"])

    return symbols


# =====================================================================
# FETCH OHLCV TF
# =====================================================================

async def fetch_tf_df(symbol: str, tf: str, limit: int = 200):
    client = await get_client(API_KEY, API_SECRET, API_PASSPHRASE)
    raw = await client.get_klines(symbol, tf, limit)
    return to_df(raw)


# =====================================================================
# MACRO CACHE
# =====================================================================

MACRO_CACHE = {"ts": 0, "BTC": None}
MACRO_TTL = 120

async def fetch_macro():
    now = time.time()
    if now - MACRO_CACHE["ts"] < MACRO_TTL:
        return MACRO_CACHE

    client = await get_client(API_KEY, API_SECRET, API_PASSPHRASE)
    raw = await client.get_klines("BTCUSDT_UMCBL", "1H", limit=200)

    MACRO_CACHE.update({
        "ts": now,
        "BTC": to_df(raw),
        "TOTAL": None,
        "TOTAL2": None,
    })

    return MACRO_CACHE


# =====================================================================
# PROCESS A SINGLE SYMBOL
# =====================================================================

async def process_symbol(symbol: str, analyzer: SignalAnalyzer, trader: BitgetTrader):
    try:
        df_h1 = await fetch_tf_df(symbol, "1H")
        df_h4 = await fetch_tf_df(symbol, "4H")

        if df_h1.empty or df_h4.empty or len(df_h1) < 80:
            return

        macro = await fetch_macro()

        # Analyze (ASYNC)
        result = await analyzer.analyze(symbol, df_h1, df_h4)

        if not result or not result.get("valid"):
            return

        # Extract signal
        bias = result["bias"]
        entry = result["entry"]
        sl = result["sl"]
        tp1 = result["tp1"]
        rr = result["rr"]

        # Sizing already done inside analyzer
        qty = result.get("qty", None)
        if qty is None:
            LOGGER.error(f"No qty returned by analyzer for {symbol}")
            return

        LOGGER.warning(f"🎯 {symbol} — {bias} @ {entry} (RR {rr:.2f})")

        # Telegram
        await send_telegram(
            f"🚀 *Signal confirmé — Desk Lead*\n"
            f"• **{symbol}** — *{bias}*\n"
            f"• Entrée: `{entry}`\n"
            f"• SL: `{sl}`\n"
            f"• TP1: `{tp1}`\n"
            f"• RR: `{rr:.2f}`\n"
            f"• Qty: `{qty}`\n"
            f"• Inst score: `{result.get('institutional_score')}`\n"
        )

        # ===============================
        # EXECUTION (Desk Lead Mode)
        # ===============================

        entry_res = await trader.place_limit(symbol, bias, entry, qty)
        if entry_res.get("code") != "00000":
            LOGGER.error(f"Entry failed for {symbol}: {entry_res}")
            return

        await trader.place_stop_loss(symbol, bias, sl, qty)
        await trader.place_take_profit(symbol, bias, tp1, qty * 0.5)

        tp2 = result.get("tp2")
        if tp2:
            await trader.place_take_profit(symbol, bias, tp2, qty * 0.5)

    except Exception as e:
        LOGGER.error(f"[{symbol}] process_symbol error: {e}")


# =====================================================================
# MAIN LOOP
# =====================================================================

async def scan_loop():
    client = await get_client(API_KEY, API_SECRET, API_PASSPHRASE)
    trader = BitgetTrader(API_KEY, API_SECRET, API_PASSPHRASE)
    analyzer = SignalAnalyzer(API_KEY, API_SECRET, API_PASSPHRASE)

    while True:
        try:
            LOGGER.info("=== START SCAN ===")

            symbols = await fetch_all_symbols_bitget(client)
            tasks = [process_symbol(s, analyzer, trader) for s in symbols]

            await asyncio.gather(*tasks)

            LOGGER.info("=== END SCAN ===")

        except Exception as e:
            LOGGER.error(f"SCAN ERROR: {e}")

        await asyncio.sleep(SCAN_INTERVAL_MIN * 60)


# ============================================================
# MAIN ENTRY — ASYNC SAFE FOR RAILWAY
# ============================================================

# ============================================================
# MAIN ENTRYPOINT COMPATIBLE RAILWAY
# ============================================================

async def start_scanner():
    """Entrypoint utilisé par main.py"""
    await run_scanner()


# Option exécutable en local
if __name__ == "__main__":
    try:
        asyncio.run(start_scanner())
    except RuntimeError:
        # Si une boucle existe déjà (cas PTB / environnements managés)
        loop = asyncio.get_event_loop()
        loop.create_task(start_scanner())
        loop.run_forever()

