# =====================================================================
# scanner.py — Scanner ASYNC institutionnel pour Bitget Futures
# =====================================================================
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


# ============================================================
# LOADING KLINES
# ============================================================
async def load_klines(symbol: str, tf: str, limit: int = 200) -> pd.DataFrame:
    client = await get_client(API_KEY, API_SECRET, API_PASSPHRASE)
    data = await client.get_klines(symbol, tf, limit)
    if not data:
        return pd.DataFrame()

    # Bitget candles: [timestamp, open, high, low, close, volume]
    df = pd.DataFrame(data, columns=["ts", "open", "high", "low", "close", "volume"])
    df[["open", "high", "low", "close", "volume"]] = df[["open", "high", "low", "close", "volume"]].astype(float)
    return df


# ============================================================
# MAIN SCAN LOGIC
# ============================================================
async def scan_symbol(
    symbol: str,
    analyzer: SignalAnalyzer,
    trader: BitgetTrader,
    risk: RiskManager,
    dup: DuplicateGuard,
):
    try:
        # --------------------------------------------------------------
        # 1) Load H1 & H4
        # --------------------------------------------------------------
        df_h1 = await load_klines(symbol, "1h", 200)
        df_h4 = await load_klines(symbol, "4h", 200)

        if df_h1.empty or df_h4.empty:
            return

        # --------------------------------------------------------------
        # 2) Load contract info
        # --------------------------------------------------------------
        client = await get_client(API_KEY, API_SECRET, API_PASSPHRASE)
        contract = await client.get_contract(symbol)
        if not contract:
            return

        # --------------------------------------------------------------
        # 3) Analyze signal
        # --------------------------------------------------------------
        signal = await analyzer.analyze(symbol, df_h1, df_h4, contract)
        if not signal:
            return

        side = signal["side"]
        entry = signal["entry"]
        sl = signal["sl"]
        tp1 = signal["tp1"]
        rr = signal["rr"]

        # Duplicate check
        fp = f"{symbol}-{side}-{round(entry,5)}-{round(sl,5)}"
        if dup.seen(fp):
            return

        # --------------------------------------------------------------
        # 4) Risk Manager
        # --------------------------------------------------------------
        allowed, reason = risk.can_trade(side)
        if not allowed:
            return

        # --------------------------------------------------------------
        # 5) Position sizing
        # --------------------------------------------------------------
        multiplier = float(contract.get("size", 0.001))
        lot_size = float(contract.get("size", 0.001))  # Minimal lot

        qty = compute_position_size(
            entry=entry,
            stop=sl,
            risk_usdt=risk.risk_for_this_trade(),
            lot_multiplier=multiplier,
            lot_size=lot_size,
        )

        if qty <= 0:
            return

        # --------------------------------------------------------------
        # 6) Place LIMIT ENTRY
        # --------------------------------------------------------------
        r_entry = await trader.place_limit(symbol, side, entry, qty)
        if "error" in r_entry or r_entry.get("code") not in ["00000", 200, None]:
            return

        risk.register_trade(side)

        # --------------------------------------------------------------
        # 7) Place SL & TP1
        # --------------------------------------------------------------
        await trader.place_stop_loss(symbol, side, sl, qty)
        await trader.place_take_profit(symbol, side, tp1, qty * 0.5)

        # --------------------------------------------------------------
        # 8) Telegram notification
        # --------------------------------------------------------------
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

        print(f"Trade envoyé sur {symbol} — {side} — {entry}")

    except Exception as e:
        print(f"[ERROR] scan_symbol {symbol}: {e}")


# ============================================================
# MAIN LOOP
# ============================================================
API_KEY = ""
API_SECRET = ""
API_PASSPHRASE = ""

SYMBOLS = [
    "BTCUSDT_UMCBL",
    "ETHUSDT_UMCBL",
    "SOLUSDT_UMCBL",
    "ADAUSDT_UMCBL",
]

risk_manager = RiskManager()
duplicate_guard = DuplicateGuard()


async def run_scanner():
    analyzer = SignalAnalyzer(API_KEY, API_SECRET, API_PASSPHRASE)
    trader = BitgetTrader(API_KEY, API_SECRET, API_PASSPHRASE)

    while True:
        tasks = []
        for sym in SYMBOLS:
            tasks.append(
                scan_symbol(sym, analyzer, trader, risk_manager, duplicate_guard)
            )
        await asyncio.gather(*tasks)

        await asyncio.sleep(60)  # scan every minute


if __name__ == "__main__":
    asyncio.run(run_scanner())
