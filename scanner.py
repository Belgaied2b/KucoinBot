import os
import json
import time
import asyncio
import pandas as pd
from datetime import datetime
from telegram import Bot
from kucoin_utils import fetch_symbols, fetch_klines
from signal_analysis import analyze_signal
from graph import generate_chart

# Chargement des signaux déjà envoyés
if os.path.exists("sent_signals.json"):
    with open("sent_signals.json", "r") as f:
        sent_signals = json.load(f)
else:
    sent_signals = {}

# Fonction principale de scan
async def scan_and_send_signals(bot: Bot, chat_id: str):
    print(f"--- Scan lancé à {datetime.utcnow().strftime('%Y-%m-%d %H:%M:%S')} UTC ---")

    symbols = fetch_symbols()
    print(f"Nombre de paires analysées : {len(symbols)}")

    for symbol in symbols:
        try:
            df = fetch_klines(symbol, interval='1h', limit=200)
            if df is None or len(df) < 100:
                continue

            # 🔍 Filtres pro : BTC, COS, BOS
            if not is_btc_favorable():
                print(f"[{symbol}] ❌ BTC pas favorable — signal ignoré.")
                continue

            if not is_cos_valid(df):
                print(f"[{symbol}] ❌ COS non détecté — pas de structure haussière.")
                continue

            if not is_bos_valid(df):
                print(f"[{symbol}] ❌ BOS non validé — pas de cassure structurelle.")
                continue

            signal = analyze_signal(df)

            if signal:
                signal_id = f"{symbol}-{signal['type']}"

                if signal_id in sent_signals:
                    print(f"[{symbol}] 🔁 Signal déjà envoyé ({signal['type']})")
                    continue

                image_path = generate_chart(df, signal)

                message = f"""
{symbol} - Signal {signal['type']} ({signal['direction']})

🔵 Entrée idéale : {signal['entry']}
🛑 SL : {signal['sl']}
🎯 TP : {signal['tp']}
📈 {signal['comment']}
""".strip()

                await bot.send_photo(chat_id=chat_id, photo=open(image_path, 'rb'), caption=message)

                sent_signals[signal_id] = datetime.utcnow().isoformat()
                with open("sent_signals.json", "w") as f:
                    json.dump(sent_signals, f, indent=2)

                print(f"[{symbol}] ✅ Signal envoyé : {signal['type']}")

        except Exception as e:
            print(f"[{symbol}] ⚠️ Erreur : {e}")

# ===================== 🔥 AJOUT : Filtres PRO COS + BOS 🔥 ===================== #

def is_bos_valid(df):
    recent_high = df['high'].iloc[-5:-1].max()
    current_close = df['close'].iloc[-1]
    return current_close > recent_high

def is_cos_valid(df):
    higher_lows = df['low'].iloc[-6] < df['low'].iloc[-4] < df['low'].iloc[-2]
    higher_highs = df['high'].iloc[-6] < df['high'].iloc[-4] < df['high'].iloc[-2]
    return higher_lows and higher_highs

def rsi(series, period=14):
    delta = series.diff()
    gain = delta.where(delta > 0, 0).rolling(window=period).mean()
    loss = -delta.where(delta < 0, 0).rolling(window=period).mean()
    rs = gain / loss
    return 100 - (100 / (1 + rs))

def macd(series, short=12, long=26, signal=9):
    ema_short = series.ewm(span=short, adjust=False).mean()
    ema_long = series.ewm(span=long, adjust=False).mean()
    macd_line = ema_short - ema_long
    signal_line = macd_line.ewm(span=signal, adjust=False).mean()
    return macd_line, signal_line

def is_btc_favorable():
    try:
        df = fetch_klines('BTC/USDT:USDT', interval='1h', limit=100)
        df['rsi'] = rsi(df['close'])
        df['macd'], df['signal'] = macd(df['close'])
        return df['rsi'].iloc[-1] > 50 and df['macd'].iloc[-1] > df['signal'].iloc[-1]
    except:
        return True  # fail-safe
