# scanner.py

import ccxt
import pandas as pd
import pandas_ta as ta
from plot_signal import generate_trade_chart
from config import CHAT_ID
import os
import datetime

exchange = ccxt.kucoinfutures()

async def scan_and_send_signals(bot):
    print("🚀 Début du scan")
    markets = await exchange.load_markets()
    symbols = [s for s in markets if markets[s].get("contract") and markets[s].get("linear")]
    print(f"📊 {len(symbols)} PERP détectés")

    for symbol in symbols:
        df = await fetch_ohlcv(symbol)
        if df is None or df.empty:
            continue

        signal_data = analyze(df)
        if signal_data:
            direction = signal_data['direction']
            entry = signal_data['entry']
            sl = signal_data['sl']
            tp = signal_data['tp']
            precision = 4
            entry_str = f"{entry:.{precision}f}"
            sl_str = f"{sl:.{precision}f}"
            tp_str = f"{tp:.{precision}f}"

            # Générer le graphique
            filename = f"chart_{symbol.replace('/', '_')}.png"
            generate_trade_chart(df, symbol, entry, sl, tp, filename)

            msg = f"🟢 {direction} sur {symbol}\n🎯 Entrée : {entry_str}\n⛔ SL : {sl_str}\n💰 TP : {tp_str}"
            await bot.send_photo(chat_id=CHAT_ID, photo=open(filename, 'rb'), caption=msg)
            os.remove(filename)

async def fetch_ohlcv(symbol, timeframe="4h", limit=100):
    try:
        ohlcv = await exchange.fetch_ohlcv(symbol, timeframe, limit)
        df = pd.DataFrame(ohlcv, columns=["timestamp", "open", "high", "low", "close", "volume"])
        df["timestamp"] = pd.to_datetime(df["timestamp"], unit="ms")
        return df
    except Exception as e:
        print(f"Erreur fetch {symbol}: {e}")
        return None

def analyze(df):
    df["rsi"] = ta.rsi(df["close"], length=14)
    macd = ta.macd(df["close"])
    if macd is None or "MACD_12_26_9" not in macd or "MACDs_12_26_9" not in macd:
        return None

    df["macd"] = macd["MACD_12_26_9"]
    df["macd_signal"] = macd["MACDs_12_26_9"]
    last = df.dropna().iloc[-1]
    price = last["close"]

    # Fibo
    high = df["close"].max()
    low = df["close"].min()
    fib_0618 = high - (high - low) * 0.618
    fib_0382 = high - (high - low) * 0.382
    in_zone = fib_0618 <= price <= fib_0382

    # Long
    if last["rsi"] < 45 and last["macd"] > last["macd_signal"] and in_zone:
        sl = df["low"].tail(5).min()
        entry = price * 0.995  # entrée idéale sous le prix
        tp = entry + 2 * (entry - sl)
        return {"direction": "LONG", "entry": entry, "sl": sl, "tp": tp}

    # Short
    if last["rsi"] > 55 and last["macd"] < last["macd_signal"] and in_zone:
        sl = df["high"].tail(5).max()
        entry = price * 1.005  # entrée idéale au-dessus du prix
        tp = entry - 2 * (sl - entry)
        return {"direction": "SHORT", "entry": entry, "sl": sl, "tp": tp}

    return None
