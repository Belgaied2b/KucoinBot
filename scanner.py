# scanner.txt (anciennement scanner.py)

import ccxt
import pandas as pd
import pandas_ta as ta
import datetime
from config import CHAT_ID
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
    exchange = ccxt.kucoinf