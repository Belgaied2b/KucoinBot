import os
import time
import pandas as pd
import requests
import matplotlib.pyplot as plt
from kucoin_utils import get_all_symbols_from_kucoin
from analysis import analyze_symbol
from graph import generate_trade_graph
from telegram import Bot

CHAT_ID = os.getenv("CHAT_ID")
TOKEN = os.getenv("TOKEN")
bot = Bot(token=TOKEN)

def scan_and_send_signals():
    print("🚀 Début du scan automatique")
    symbols = get_all_symbols_from_kucoin()
    contracts = [s for s in symbols if "USDT:USDT" in s and "PERP" in s]
    print(f"📉 Nombre de PERP détectés : {len(contracts)}")

    for symbol in contracts:
        try:
            df = fetch_ohlcv(symbol)
            if df is None or len(df) < 100:
                continue

            signal = analyze_symbol(symbol, df)
            if signal:
                fig = generate_trade_graph(symbol, df, signal)
                image_path = f"{symbol.replace('/', '_')}.png"
                fig.savefig(image_path)
                bot.send_photo(chat_id=CHAT_ID, photo=open(image_path, "rb"), caption=signal["message"])
                plt.close(fig)
                os.remove(image_path)

        except Exception as e:
            print(f"Erreur avec {symbol}: {e}")

    print("✅ Scan automatique terminé")

def fetch_ohlcv(symbol):
    url = f"https://api.kucoin.com/api/v1/market/candles?type=4hour&symbol={symbol.replace(':', '-')}"
    try:
        response = requests.get(url)
        data = response.json()["data"]
        if not data:
            return None
        df = pd.DataFrame(data, columns=["time", "open", "close", "high", "low", "volume", "turnover"])
        df = df.iloc[::-1]
        df[["open", "close", "high", "low", "volume"]] = df[["open", "close", "high", "low", "volume"]].astype(float)
        df["time"] = pd.to_datetime(df["time"], unit="s")
        return df
    except Exception as e:
        print(f"Erreur récupération OHLCV {symbol}: {e}")
        return None
