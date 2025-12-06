# =====================================================================
# scanner.py — Scanner ASYNC institutionnel pour Bitget Futures
# Version corrigée et optimisée (5 min, 80 pairs, API Railway)
# =====================================================================

import os
import asyncio
import pandas as pd
from typing import List, Dict, Any

from bitget_client import get_client
from bitget_trader import BitgetTrader
from analyze_signal import SignalAnalyzer
from sizing import compute_position_size
from risk_manager import RiskManager
from duplicate_guard import DuplicateGuard
from telegram_client import send_telegram_message


# =====================================================================
# CHARGEMENT DES VARIABLES RAILWAY
# =====================================================================
API_KEY = os.getenv("API_KEY", "")
API_SECRET = os.getenv("API_SECRET", "")
API_PASSPHRASE = os.getenv("API_PASSPHRASE", "")

# 80 paires les plus courantes Bitget USDT-M
SYMBOLS = [
    "BTCUSDT_UMCBL","ETHUSDT_UMCBL","SOLUSDT_UMCBL","ADAUSDT_UMCBL","XRPUSDT_UMCBL",
    "BNBUSDT_UMCBL","AVAXUSDT_UMCBL","LINKUSDT_UMCBL","DOTUSDT_UMCBL","MATICUSDT_UMCBL",
    "DOGEUSDT_UMCBL","SHIBUSDT_UMCBL","ARBUSDT_UMCBL","OPUSDT_UMCBL","APTUSDT_UMCBL",
    "ATOMUSDT_UMCBL","SUIUSDT_UMCBL","NEARUSDT_UMCBL","FILUSDT_UMCBL","ETCUSDT_UMCBL",
    "ICPUSDT_UMCBL","GRTUSDT_UMCBL","AAVEUSDT_UMCBL","LDOUSDT_UMCBL","INJUSDT_UMCBL",
    "UNIUSDT_UMCBL","MKRUSDT_UMCBL","CRVUSDT_UMCBL","SNXUSDT_UMCBL","EOSUSDT_UMCBL",
    "XTZUSDT_UMCBL","SANDUSDT_UMCBL","MANAUSDT_UMCBL","THETAUSDT_UMCBL","RPLUSDT_UMCBL",
    "DYDXUSDT_UMCBL","LTCUSDT_UMCBL","CELOUSDT_UMCBL","ROSEUSDT_UMCBL","STXUSDT_UMCBL",
    "FLOWUSDT_UMCBL","FTMUSDT_UMCBL","WLDUSDT_UMCBL","XLMUSDT_UMCBL","XMRUSDT_UMCBL",
    "ZECUSDT_UMCBL","COMPUSDT_UMCBL","BATUSDT_UMCBL","ENJUSDT_UMCBL","IMXUSDT_UMCBL",
    "AGIXUSDT_UMCBL","LRCUSDT_UMCBL","1INCHUSDT_UMCBL","MASKUSDT_UMCBL","PEPEUSDT_UMCBL",
    "BONKUSDT_UMCBL","JUPUSDT_UMCBL","SEIUSDT_UMCBL","PYTHUSDT_UMCBL","ACEUSDT_UMCBL",
    "ONDOUSDT_UMCBL","YGGUSDT_UMCBL","GALUSDT_UMCBL","IDUSDT_UMCBL","BRISEUSDT_UMCBL",
    "ZILUSDT_UMCBL","KAVAUSDT_UMCBL","SCUSDT_UMCBL","IOTAUSDT_UMCBL","GMTUSDT_UMCBL",
    "MINAUSDT_UMCBL","HOTUSDT_UMCBL","BLURUSDT_UMCBL","TIAUSDT_UMCBL","STRKUSDT_UMCBL"
]


# =====================================================================
# LOAD KLINES
# =====================================================================
async def load_klines(symbol: str, tf: str, limit: int = 200) -> pd.DataFrame:
    client = await get_client(API_KEY, API_SECRET, API_PASSPHRASE)

    candles = await client.get_klines(symbol, tf, limit)
    if not candles:
        return pd.DataFrame()

    df = pd.DataFrame(candles, columns=["ts", "open", "high", "low", "close", "volume"])
    df[["open", "high", "low", "close", "volume"]] = df[["open", "high", "low", "close", "volume"]].astype(float)
    return df


# =====================================================================
# SCAN LOGIC
# =====================================================================
async def scan_symbol(
    symbol: str,
    analyzer: SignalAnalyzer,
    trader: BitgetTrader,
    risk: RiskManager,
    dup: DuplicateGuard,
):
    try:
        print(f"🔎 Scanning {symbol} ...")

        # ---------------------------------------------------------
        # KLINES
        # ---------------------------------------------------------
        df_h1 = await load_klines(symbol, "1h", 200)
        df_h4 = await load_klines(symbol, "4h", 200)

        if df_h1.empty or df_h4.empty:
            print(f"⚠️ {symbol} — Impossible de charger H1/H4")
            return

        # ---------------------------------------------------------
        # CONTRACT INFO
        # ---------------------------------------------------------
        client = await get_client(API_KEY, API_SECRET, API_PASSPHRASE)
        contract = await client.get_contract(symbol)

        if not contract:
            print(f"⚠️ {symbol} — Pas d'info contrat.")
            return

        # ---------------------------------------------------------
        # ANALYZE SIGNAL
        # ---------------------------------------------------------
        signal = await analyzer.analyze(symbol, df_h1, df_h4, contract)
        if not signal:
            return

        side = signal["side"]
        entry = signal["entry"]
        sl = signal["sl"]
        tp1 = signal["tp1"]
        rr = signal["rr"]

        # Duplicate Guard
        fp = f"{symbol}-{side}-{round(entry,5)}-{round(sl,5)}"
        if dup.seen(fp):
            return

        # ---------------------------------------------------------
        # RISK MANAGER
        # ---------------------------------------------------------
        allowed, reason = risk.can_trade(side)
        if not allowed:
            print(f"🚫 {symbol} — Trade refusé ({reason})")
            return

        # ---------------------------------------------------------
        # SIZING
        # ---------------------------------------------------------
        multiplier = float(contract.get("size", 0.001))
        lot_size = float(contract.get("size", 0.001))

        qty = compute_position_size(
            entry=entry,
            stop=sl,
            risk_usdt=risk.risk_for_this_trade(),
            lot_multiplier=multiplier,
            lot_size=lot_size,
        )

        if qty <= 0:
            print(f"⚠️ {symbol} — qty <= 0, abort.")
            return

        # ---------------------------------------------------------
        # PLACE LIMIT ORDER
        # ---------------------------------------------------------
        r_entry = await trader.place_limit(symbol, side, entry, qty)

        if "error" in r_entry or r_entry.get("code") not in ["00000", 200, None]:
            print(f"❌ {symbol} — Erreur entrée : {r_entry}")
            return

        risk.register_trade(side)

        # ---------------------------------------------------------
        # PLACE SL/TP
        # ---------------------------------------------------------
        await trader.place_stop_loss(symbol, side, sl, qty)
        await trader.place_take_profit(symbol, side, tp1, qty * 0.5)

        # ---------------------------------------------------------
        # TELEGRAM
        # ---------------------------------------------------------
        msg = (
            f"📈 *Signal détecté*\n\n"
            f"Symbol: `{symbol}`\n"
            f"Side: *{side}*\n"
            f"Entry: `{entry}`\n"
            f"SL: `{sl}`\n"
            f"TP1: `{tp1}`\n"
            f"RR: `{round(rr,2)}`\n"
            f"Qty: `{qty}`\n"
            f"Inst Score: `{signal['institutional']['score']}`\n"
            f"OTE: `{signal['ote']}`"
        )
        send_telegram_message(msg)

        print(f"✅ Trade envoyé : {symbol} — {side} @ {entry}")

    except Exception as e:
        print(f"[ERROR] scan_symbol {symbol}: {e}")


# =====================================================================
# MAIN LOOP (SCAN EVERY 5 MINUTES)
# =====================================================================
risk_manager = RiskManager()
duplicate_guard = DuplicateGuard()

SCAN_INTERVAL = 300  # 5 minutes


async def run_scanner():
    analyzer = SignalAnalyzer(API_KEY, API_SECRET, API_PASSPHRASE)
    trader = BitgetTrader(API_KEY, API_SECRET, API_PASSPHRASE)

    print("🚀 Scanner Bitget démarré. Scan toutes les 5 minutes...")

    while True:
        tasks = [scan_symbol(sym, analyzer, trader, risk_manager, duplicate_guard) for sym in SYMBOLS]
        await asyncio.gather(*tasks)

        print(f"⏳ Pause de {SCAN_INTERVAL} secondes...\n")
        await asyncio.sleep(SCAN_INTERVAL)


if __name__ == "__main__":
    asyncio.run(run_scanner())
