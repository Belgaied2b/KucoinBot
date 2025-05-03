# scanner.py

import os
import ccxt
import pandas as pd
import pandas_ta as ta
import matplotlib.pyplot as plt
from datetime import datetime
from graph import generate_trade_graph
from telegram import Bot

# Variables Railway
TOKEN = os.getenv("TOKEN")
CHAT_ID = os.getenv("CHAT_ID")

bot = Bot(token=TOKEN)
exchange = ccxt.kucoinfutures()

sent_signals = set()

def fetch_ohlcv(symbol):
    try:
        ohlcv = exchange.fetch_ohlcv(symbol, timeframe='4h', limit=100)
        df = pd.DataFrame(ohlcv, columns=["timestamp", "open", "high", "low", "close", "volume"])
        df["timestamp"] = pd.to_datetime(df["timestamp"], unit="ms")
        return df
    except Exception as e:
        print(f"Erreur fetch {symbol}: {e}")
        return None

def analyze(df, symbol):
    df["rsi"] = ta.rsi(df["close"], length=14)
    macd = ta.macd(df["close"])
    if macd is None or macd.isnull().values.any():
        return None
    df["macd"] = macd["MACD_12_26_9"]
    df["macd_signal"] = macd["MACDs_12_26_9"]

    rsi_value = df["rsi"].iloc[-1]
    if not (40 <= rsi_value <= 60):
        return None
    if df["macd"].iloc[-1] < df["macd_signal"].iloc[-1]:
        return None
    if df["volume"].iloc[-1] < 1000000:
        return None

    recent_high = df["high"].iloc[-20:].max()
    recent_low = df["low"].iloc[-20:].min()
    fib_0 = recent_high
    fib_1 = recent_low
    fib_0_5 = fib_1 + 0.5 * (fib_0 - fib_1)
    fib_0_618 = fib_1 + 0.618 * (fib_0 - fib_1)
    price = df["close"].iloc[-1]

    if not (fib_0_5 <= price <= fib_0_618):
        return None

    entry = round(price * 0.995, 4)
    sl = round(df["low"].iloc[-5:].min() * 0.995, 4)
    tp = round(entry + (entry - sl) * 2, 4)
    direction = "LONG"

    return {
        "symbol": symbol,
        "entry": entry,
        "sl": sl,
        "tp": tp,
        "direction": direction,
        "price": round(price, 4)
    }

async def scan_and_send_signals(bot_instance):
    try:
        markets = exchange.load_markets()
        symbols = [s for s in markets if s.endswith(":USDT") and "perpetual" in markets[s].get("info", {}).get("contractType", "").lower()]
        print(f"📉 Nombre de PERP détectés : {len(symbols)}")

        for symbol in symbols:
            df = fetch_ohlcv(symbol)
            if df is None or len(df) < 50:
                continue
            signal = analyze(df, symbol)
            if signal:
                key = f"{signal['symbol']}_{signal['direction']}_{signal['entry']}"
                if key in sent_signals:
                    continue
                sent_signals.add(key)
                img_path = generate_trade_graph(df, signal)
                msg = f"📈 Signal {signal['direction']} détecté sur {signal['symbol']}\n"
                msg += f"🎯 Entrée : {signal['entry']}\n🎯 TP : {signal['tp']}\n❌ SL : {signal['sl']}"
                with open(img_path, "rb") as photo:
                    await bot_instance.send_photo(chat_id=CHAT_ID, photo=photo, caption=msg)
        print("✅ Scan terminé")
    except Exception as e:
        print(f"Erreur scan: {e}")
