# =====================================================================
# scanner.py — Bitget Desk Lead Scanner (Institutionnel H1+H4, API V2)
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


# ============================================================
# TELEGRAM
# ============================================================

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


# ============================================================
# OHLCV → DF
# ============================================================

def to_df(raw):
    if raw is None or len(raw) == 0:
        return pd.DataFrame()

    try:
        df = pd.DataFrame(
            raw,
            columns=["timestamp", "open", "high", "low", "close", "volume"]
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
    except:
        return pd.DataFrame()


# ============================================================
# FETCH SYMBOLS BITGET (API V2)
# ============================================================

async def fetch_all_symbols_bitget(client):
    """
    Nouvelle API Bitget V2 :
    productType attendu = "USDT-FUTURES"
    """
    r = await client._request(
        "GET",
        "/api/v2/mix/market/contracts",
        params={"productType": "USDT-FUTURES"},
        auth=False
    )

    LOGGER.error(f"📡 RAW CONTRACTS RESPONSE: {r}")

    syms = []
    data = r.get("data") or []

    for c in data:
        if c.get("quoteCoin") == "USDT":
            syms.append(c["symbol"])

    return syms


# ============================================================
# MULTI-TF DATA
# ============================================================

async def fetch_tf_df(symbol: str, tf: str):
    client = await get_client(API_KEY, API_SECRET, API_PASSPHRASE)
    return await client.get_klines_df(symbol, tf, 200)


# ============================================================
# MACRO CACHE
# ============================================================

MACRO_CACHE = {"ts": 0, "BTC": None}
MACRO_TTL = 120

async def fetch_macro_data():
    now = time.time()
    if now - MACRO_CACHE["ts"] < MACRO_TTL:
        return MACRO_CACHE

    client = await get_client(API_KEY, API_SECRET, API_PASSPHRASE)
    df_btc = await client.get_klines_df("BTCUSDTM", "1H", 200)

    MACRO_CACHE.update({
        "ts": now,
        "BTC": df_btc,
        "TOTAL": None,
        "TOTAL2": None,
    })

    return MACRO_CACHE


# ============================================================
# PROCESS SYMBOL
# ============================================================

async def process_symbol(symbol: str, analyzer: SignalAnalyzer, trader: BitgetTrader):
    try:
        df_h1 = await fetch_tf_df(symbol, "1H")
        df_h4 = await fetch_tf_df(symbol, "4H")

        if df_h1.empty or df_h4.empty or len(df_h1) < 80:
            return

        macro = await fetch_macro_data()

        result = await analyzer.analyze(symbol, df_h1, df_h4, macro)

        if not result or "signal" not in result:
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
            f"• Side: *{side}*\n"
            f"• Entry: `{entry}`\n"
            f"• SL:`{sl}` | TP1:`{tp1}` | TP2:`{tp2}`\n"
            f"• Qty:`{qty}`\n"
        )

        entry_res = await trader.place_limit(symbol, side, entry, qty)
        if entry_res.get("code") != "00000":
            LOGGER.error(f"[ERROR ENTRY] {symbol}: {entry_res}")
            return

        await trader.place_stop_loss(symbol, side, sl, qty)

        if tp1:
            await trader.place_take_profit(symbol, side, tp1, qty * 0.5)
        if tp2:
            await trader.place_take_profit(symbol, side, tp2, qty * 0.5)

    except Exception as e:
        LOGGER.error(f"[{symbol}] ERROR: {e}")


# ============================================================
# SCAN LOOP
# ============================================================

async def run_scanner():
    client = await get_client(API_KEY, API_SECRET, API_PASSPHRASE)
    trader = BitgetTrader(API_KEY, API_SECRET, API_PASSPHRASE)
    analyzer = SignalAnalyzer(API_KEY, API_SECRET, API_PASSPHRASE)

    while True:
        try:
            LOGGER.info("=== START SCAN ===")

            symbols = await fetch_all_symbols_bitget(client)

            if not symbols:
                LOGGER.warning("⚠️ Bitget returned empty symbol list — fallback BTC/ETH")
                symbols = ["BTCUSDTM", "ETHUSDTM"]

            tasks = [process_symbol(s, analyzer, trader) for s in symbols]
            await asyncio.gather(*tasks)

            LOGGER.info("=== END SCAN ===")

        except Exception as e:
            LOGGER.error(f"SCAN ERROR: {e}")

        await asyncio.sleep(SCAN_INTERVAL_MIN * 60)


async def start_scanner():
    await run_scanner()


if __name__ == "__main__":
    asyncio.run(start_scanner())
