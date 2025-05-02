# scanner.py

import ccxt
import pandas as pd
import pandas_ta as ta
from config import CHAT_ID
import datetime
import os

SENT_SIGNALS_FILE = "sent_signals.csv"


def load_sent_signals():
    if os.path.exists(SENT_SIGNALS_FILE):
        return pd.read_csv(SENT_SIGNALS_FILE)
    return pd.DataFrame(columns=["symbol", "side"])


def save_sent_signal(symbol, side):
    df = load_sent_signals()
    if not ((df["symbol"] == symbol) & (df["side"] == side)).any():
        df = pd.concat([df, pd.DataFrame([{"symbol": symbol, "side": side}])])
        df.to_csv(SENT_SIGNALS_FILE, index=False)


def signal_exists(symbol, side):
    df = load_sent_signals()
    return ((df["symbol"] == symbol) & (df["side"] == side)).any()


def get_kucoin_symbols():
    exchange = ccxt.kucoinfutures()
    markets = exchange.load_markets()
    return [symbol for symbol in markets if symbol.endswith(":USDTM")]


def fetch_ohlcv(symbol, timeframe="4h", limit=100):
    exchange = ccxt.kucoinfutures()
    ohlcv = exchange.fetch_ohlcv(symbol, timeframe=timeframe, limit=limit)
    df = pd.DataFrame(ohlcv, columns=["timestamp", "open", "high", "low", "close", "volume"])
    df["timestamp"] = pd.to_datetime(df["timestamp"], unit="ms")
    return df


def analyze(df):
    df["rsi"] = ta.rsi(df["close"], length=14)
    macd = ta.macd(df["close"])
    df["macd"] = macd["MACD_12_26_9"]
    df["macd_signal"] = macd["MACDs_12_26_9"]

    last = df.iloc[-1]

    long_signal = (
        last["rsi"] < 70
        and last["macd"] > last["macd_signal"]
    )

    short_signal = (
        last["rsi"] > 30
        and last["macd"] < last["macd_signal"]
    )

    if long_signal:
        return "LONG"
    elif short_signal:
        return "SHORT"
    else:
        return None


async def scan_and_send_signals(bot):
    symbols = get_kucoin_symbols()
    for symbol in symbols:
        try:
            df = fetch_ohlcv(symbol)
            signal = analyze(df)
            if signal and not signal_exists(symbol, signal):
                message = f"{signal} 🚀 {symbol.replace(':USDTM', '')} | 4H"
                await bot.send_message(chat_id=CHAT_ID, text=message)
                save_sent_signal(symbol, signal)
        except Exception as e:
            print(f"[Erreur] {symbol}: {e}")
