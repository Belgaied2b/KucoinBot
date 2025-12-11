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
from duplicate_guard import DuplicateGuard
from risk_manager import RiskManager
from retry_utils import retry_async

LOGGER = logging.getLogger(__name__)

# =====================================================================
# TELEGRAM (singleton)
# =====================================================================

TELEGRAM_APP: Application | None = None

# Anti-doublons et Risk Manager globaux
DUP_GUARD = DuplicateGuard(ttl_seconds=3600)
RISK_MANAGER = RiskManager()


async def init_telegram():
    """Initialise l'Application Telegram une seule fois."""
    global TELEGRAM_APP
    if TELEGRAM_APP is None:
        TELEGRAM_APP = Application.builder().token(TELEGRAM_BOT_TOKEN).build()
        await TELEGRAM_APP.initialize()
        await TELEGRAM_APP.start()


async def send_telegram(text: str):
    """Envoie un message Telegram, avec gestion d'erreurs soft."""
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
# OHLCV normalisation (optionnel, si besoin ailleurs)
# =====================================================================

def to_df(raw):
    if not raw:
        return pd.DataFrame()
    df = pd.DataFrame(raw, columns=["time", "open", "high", "low", "close", "volume"])
    df = df.astype(float)
    return df.sort_values("time").reset_index(drop=True)


# =====================================================================
# PROCESSING SYMBOL
# =====================================================================

async def process_symbol(symbol: str, analyzer: SignalAnalyzer, trader: BitgetTrader, client):
    """
    Pipeline complet pour un symbole :
      - Récup H1 / H4
      - Analyse structure + insti (analyze_signal.SignalAnalyzer)
      - Envoi Telegram
      - Placement LIMIT + TP / SL sur Bitget
    """
    try:
        # ====== MARKETDATA H1 / H4 ======
        async def _fetch_h1():
            return await client.get_klines_df(symbol, "1H", 200)

        async def _fetch_h4():
            return await client.get_klines_df(symbol, "4H", 200)

        df_h1 = await retry_async(_fetch_h1)
        df_h4 = await retry_async(_fetch_h4)

        if df_h1.empty or df_h4.empty or len(df_h1) < 80:
            return

        macro = {}  # placeholder : BTC / TOTAL / DOMINANCE plus tard

        # ====== ANALYSE INSTITUTIONNELLE + STRUCTURE ======
        result = await analyzer.analyze(symbol, df_h1, df_h4, macro)

        # analyze_signal 2025 renvoie un dict avec "valid": True si signal OK
        if not result or not result.get("valid"):
            return

        side = result["side"]
        entry = float(result["entry"])
        sl = float(result["sl"])
        tp1 = result.get("tp1")
        tp2 = result.get("tp2")
        qty = float(result["qty"])
        inst_score = result.get("institutional_score", None)
        rr = result.get("rr")

        # Normalisation LONG/SHORT pour RiskManager / DuplicateGuard
        side_upper = str(side).upper()
        direction = "LONG" if side_upper in ("BUY", "LONG") else "SHORT"

        # Anti-doublon : même symbole / même direction / même zone (entry/SL/TP1)
        fp_tp1 = tp1 if tp1 is not None else 0.0
        fingerprint = f"{symbol}-{direction}-{round(entry, 4)}-{round(sl, 4)}-{round(float(fp_tp1), 4)}"
        if DUP_GUARD.seen(fingerprint):
            LOGGER.info(f"[DUP] Skip {symbol} {direction} — déjà envoyé récemment")
            return

        # Risk manager : filtre institutionnel global
        can_trade, reason = RISK_MANAGER.can_trade(direction)
        if not can_trade:
            LOGGER.info(f"[RISK] REJECT {symbol} {direction} → {reason}")
            return

        LOGGER.warning(f"🎯 SIGNAL {symbol} → {side} @ {entry} (RR={rr})")

        # ====== TELEGRAM ======
        msg = (
            "🚀 *Signal détecté*\n"
            f"• **{symbol}**\n"
            f"• Direction: *{side}*\n"
            f"• Entrée: `{entry}`\n"
            f"• SL: `{sl}`\n"
        )
        if tp1 is not None or tp2 is not None:
            msg += f"• TP1: `{tp1}` | TP2: `{tp2}`\n"
        msg += f"• Qty: `{qty}`\n"
        if inst_score is not None:
            msg += f"• Inst Score: `{inst_score}`\n"
        if rr is not None:
            msg += f"• RR: `{round(rr, 3)}`\n"

        await send_telegram(msg)

        # ====== EXÉCUTION ORDRES BITGET ======
        # Entrée
        entry_res = await trader.place_limit(symbol, side, entry, qty)
        if not entry_res.get("ok", False):
            LOGGER.error(f"Entry error {symbol}: {entry_res}")
            return

        # On enregistre la position ouverte dans le RiskManager
        RISK_MANAGER.register_trade(direction)

        # Stop loss
        sl_res = await trader.place_stop_loss(symbol, side, sl, qty)
        if not sl_res.get("ok", False):
            LOGGER.error(f"SL error {symbol}: {sl_res}")

        # Take profits
        if tp1 is not None:
            tp1_res = await trader.place_take_profit(symbol, side, float(tp1), qty * 0.5)
            if not tp1_res.get("ok", False):
                LOGGER.error(f"TP1 error {symbol}: {tp1_res}")

        if tp2 is not None:
            tp2_res = await trader.place_take_profit(symbol, side, float(tp2), qty * 0.5)
            if not tp2_res.get("ok", False):
                LOGGER.error(f"TP2 error {symbol}: {tp2_res}")

    except Exception as e:
        LOGGER.error(f"[{symbol}] process_symbol error: {e}")


# =====================================================================
# MAIN SCAN LOOP
# =====================================================================

async def run_scanner():
    """
    Boucle principale :
      - Récupère la liste des contrats USDT-FUTURES
      - Scan H1/H4 pour chaque symbole avec une concurrence limitée
      - Pause entre deux scans selon SCAN_INTERVAL_MIN
    """
    client = await get_client(API_KEY, API_SECRET, API_PASSPHRASE)
    trader = BitgetTrader(API_KEY, API_SECRET, API_PASSPHRASE)
    analyzer = SignalAnalyzer(API_KEY, API_SECRET, API_PASSPHRASE)

    # Limite la pression sur l'API Bitget (429)
    MAX_CONCURRENT = 8
    semaphore = asyncio.Semaphore(MAX_CONCURRENT)

    while True:
        try:
            LOGGER.info("=== START SCAN ===")

            symbols = await client.get_contracts_list()
            if not symbols:
                LOGGER.warning("⚠️ Aucun symbole récupéré → pause 60s")
                await asyncio.sleep(60)
                continue

            LOGGER.info(f"📊 Nombre de symboles à scanner : {len(symbols)}")

            async def _worker(sym: str):
                async with semaphore:
                    await process_symbol(sym, analyzer, trader, client)

            tasks = [_worker(sym) for sym in symbols]
            await asyncio.gather(*tasks)

            LOGGER.info("=== END SCAN ===")

        except Exception as e:
            LOGGER.error(f"SCAN ERROR: {e}")

        # Pause globale entre 2 scans
        await asyncio.sleep(SCAN_INTERVAL_MIN * 60)


# =====================================================================
# EXPORT MAIN (pour main.py)
# =====================================================================

async def start_scanner():
    await run_scanner()


# =====================================================================
# MODE LOCAL (standalone)
# =====================================================================

if __name__ == "__main__":
    try:
        asyncio.run(start_scanner())
    except RuntimeError:
        # Compatibilité environnements où un event loop existe déjà
        loop = asyncio.get_event_loop()
        loop.create_task(start_scanner())
        loop.run_forever()
