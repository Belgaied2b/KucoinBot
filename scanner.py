import os
import json
import ccxt
import asyncio
import telegram
import pandas as pd
from kucoin.client import Trade, Market
from kucoin.exceptions import KucoinAPIException
from signal_analysis import analyze_signal

# 📩 Telegram
BOT_TOKEN = os.getenv("TOKEN")
CHAT_ID = int(os.getenv("CHAT_ID"))
bot = telegram.Bot(token=BOT_TOKEN)

# 🔐 KuCoin API (chargée depuis Railway)
API_KEY = os.getenv("KUCOIN_API_KEY")
API_SECRET = os.getenv("KUCOIN_API_SECRET")
API_PASSPHRASE = os.getenv("KUCOIN_API_PASSPHRASE")

# ⚙️ Trade parameters
TRADE_AMOUNT = 20  # en USDT
TRADE_LEVERAGE = 3

# 📦 KuCoin clients
market = Market(key=API_KEY, secret=API_SECRET, passphrase=API_PASSPHRASE)
trade = Trade(key=API_KEY, secret=API_SECRET, passphrase=API_PASSPHRASE, is_sandbox=False)

# 📁 Fichier pour éviter les doublons
SENT_SIGNALS_FILE = "sent_signals.json"
if not os.path.exists(SENT_SIGNALS_FILE):
    with open(SENT_SIGNALS_FILE, "w") as f:
        json.dump([], f)

def load_sent_signals():
    with open(SENT_SIGNALS_FILE, "r") as f:
        return json.load(f)

def save_sent_signal(symbol):
    sent = load_sent_signals()
    sent.append(symbol)
    with open(SENT_SIGNALS_FILE, "w") as f:
        json.dump(sent, f)

# 🚀 Exécution d’un trade KuCoin
def execute_trade(symbol, direction, entry_price):
    try:
        side = "buy" if direction == "LONG" else "sell"
        order = trade.create_market_order(symbol, side, TRADE_AMOUNT, leverage=TRADE_LEVERAGE)
        return order
    except KucoinAPIException as e:
        print(f"❌ Erreur de trade sur {symbol} : {e}")
        return None

# 📊 Récupération des données OHLCV
def get_ohlcv(symbol):
    try:
        df = pd.DataFrame(market.get_kline(symbol, "1hour", 200))
        df.columns = ["timestamp", "open", "close", "high", "low", "volume", "turnover"]
        df["timestamp"] = pd.to_datetime(df["timestamp"], unit='ms')
        df = df.sort_values("timestamp")
        df.name = symbol
        return df
    except Exception as e:
        print(f"Erreur récupération données {symbol} : {e}")
        return None

# 🔍 Scan principal
async def scan_and_send_signals():
    try:
        symbols_raw = market.get_contract_symbols()
        usdt_perps = [s["symbol"] for s in symbols_raw if s["quoteCurrency"] == "USDT" and s["enableTrading"]]

        sent_signals = load_sent_signals()

        for symbol in usdt_perps:
            if symbol in sent_signals:
                continue

            df = get_ohlcv(symbol)
            if df is None or df.empty:
                continue

            result = analyze_signal(df, direction="LONG")  # On commence avec LONG
            if result.get("valid", False):
                entry_price = result["entry"]
                current_price = float(df["close"].iloc[-1])

                if abs(current_price - entry_price) / entry_price < 0.01:  # Prix proche de l'entrée
                    order = execute_trade(symbol, "LONG", entry_price)
                    if order:
                        await bot.send_message(chat_id=CHAT_ID, text=f"✅ Trade LONG exécuté sur {symbol}\nPrix : {entry_price} USDT\nMontant : {TRADE_AMOUNT} USDT\nLevier : {TRADE_LEVERAGE}x")
                        save_sent_signal(symbol)
                    else:
                        print(f"❌ Trade échoué sur {symbol}")
    except Exception as e:
        print(f"Erreur générale dans le scan : {e}")
