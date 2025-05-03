import ccxt
import pandas as pd
import pandas_ta as ta
import datetime
import httpx
import os
from telegram.constants import ParseMode
from graph import generate_trade_graph

exchange = ccxt.kucoin()

CHAT_ID = os.getenv("CHAT_ID")

def get_perp_symbols():
    markets = exchange.load_markets()
    return [m for m in markets if markets[m]['type'] == 'future' and 'USDT' in m]

def fetch_ohlcv(symbol):
    try:
        data = exchange.fetch_ohlcv(symbol, timeframe='4h', limit=100)
        df = pd.DataFrame(data, columns=['timestamp', 'open', 'high', 'low', 'close', 'volume'])
        df['timestamp'] = pd.to_datetime(df['timestamp'], unit='ms')
        return df
    except Exception as e:
        print(f"Erreur fetch_ohlcv {symbol} : {e}")
        return None

def add_indicators(df):
    df["rsi"] = ta.rsi(df["close"], length=14)
    macd = ta.macd(df["close"])
    if macd is not None:
        df["macd"] = macd["MACD_12_26_9"]
        df["signal"] = macd["MACDs_12_26_9"]
    return df

def analyze(df, symbol):
    rsi = df["rsi"].iloc[-1]
    macd = df["macd"].iloc[-1]
    signal = df["signal"].iloc[-1]
    close = df["close"].iloc[-1]
    high = df["high"].iloc[-20:].max()
    low = df["low"].iloc[-20:].min()
    fib_0618 = low + 0.618 * (high - low)

    if 40 < rsi < 60 and macd > signal and close > fib_0618:
        direction = "LONG"
        sl = low * 0.995
        tp = close * 1.02
        return {
            "symbol": symbol,
            "direction": direction,
            "entry": close,
            "sl": sl,
            "tp": tp,
            "rsi": rsi,
            "macd": macd,
            "signal": signal
        }
    return None

async def scan_and_send_signals(bot):
    symbols = get_perp_symbols()
    print(f"⏱ Début scan auto ({len(symbols)} PERP)")
    for symbol in symbols:
        df = fetch_ohlcv(symbol)
        if df is None or df.empty:
            continue
        df = add_indicators(df)
        signal = analyze(df, symbol)
        if signal:
            chart_path = generate_trade_graph(df, signal)
            caption = (
                f"📈 <b>{signal['symbol']}</b> - {signal['direction']}\n"
                f"🎯 Entrée : <code>{signal['entry']:.4f}</code>\n"
                f"📍 SL : <code>{signal['sl']:.4f}</code>\n"
                f"🏁 TP : <code>{signal['tp']:.4f}</code>\n"
                f"📊 RSI : {signal['rsi']:.2f} | MACD : {signal['macd']:.2f}"
            )
            with open(chart_path, "rb") as img:
                await bot.send_photo(chat_id=CHAT_ID, photo=img, caption=caption, parse_mode=ParseMode.HTML)
    print("✅ Scan terminé")

# 🔧 Test manuel via /scan_test
async def run_test_scan(update, context):
    print("✅ Commande /scan_test reçue")
    print("🚀 Début du scan test")

    symbols = get_perp_symbols()
    print(f"📉 Nombre de PERP détectés : {len(symbols)}")

    for symbol in symbols:
        print(f"→ fetch {symbol}")
        try:
            df = fetch_ohlcv(symbol)
            if df is None or df.empty:
                print(f"⚠️ Aucune donnée pour {symbol}")
                continue

            df = add_indicators(df)
            result = analyze(df, symbol)

            if result:
                print(f"✅ Signal détecté : {result['direction']} sur {symbol}")
        except Exception as e:
            print(f"❌ Erreur avec {symbol} : {e}")

    print("✅ Scan test terminé")
