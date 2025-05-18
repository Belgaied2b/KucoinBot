# scanner.py

import os
import json
import pandas as pd
from datetime import datetime
from kucoin_utils import fetch_symbols, fetch_klines
from signal_analysis import analyze_signal
from graph import generate_chart
from indicators import compute_rsi as rsi, compute_macd as macd, compute_atr

if os.path.exists("sent_signals.json"):
    with open("sent_signals.json", "r") as f:
        sent_signals = json.load(f)
else:
    sent_signals = {}

def is_bos_valid(df):
    recent_high = df['high'].iloc[-5:-1].max()
    current_close = df['close'].iloc[-1]
    return current_close > recent_high

def is_cos_valid(df):
    lows = df['low'].iloc[-9:]
    highs = df['high'].iloc[-9:]
    return (
        lows.iloc[0] < lows.iloc[3] < lows.iloc[6] and
        highs.iloc[0] < highs.iloc[3] < highs.iloc[6]
    )

def is_btc_favorable():
    try:
        df = fetch_klines('BTC/USDT:USDT', interval='1h', limit=100)
        df['rsi'] = rsi(df['close'])
        df['macd'], df['signal'] = macd(df['close'])
        return df['rsi'].iloc[-1] > 50 and df['macd'].iloc[-1] > df['signal'].iloc[-1]
    except:
        return True

async def scan_and_send_signals(bot, chat_id):
    print(f"\n--- Scan lancé à {datetime.utcnow().strftime('%Y-%m-%d %H:%M:%S')} UTC ---")

    symbols = fetch_symbols()
    print(f"🔍 Nombre de paires analysées : {len(symbols)}\n")

    for symbol in symbols:
        try:
            df = fetch_klines(symbol, interval='1h', limit=200)
            if df is None or len(df) < 100:
                continue

            price = df['close'].iloc[-1]
            rsi_values = rsi(df['close'])
            macd_line, signal_line = macd(df['close'])
            atr_series = compute_atr(df)
            atr = round(atr_series.iloc[-1], 6)
            ma200 = df['close'].rolling(200).mean().iloc[-1]
            last_rsi = round(rsi_values.iloc[-1], 2)
            last_macd = round(macd_line.iloc[-1], 6)
            last_signal = round(signal_line.iloc[-1], 6)
            last_ma200 = round(ma200, 6)

            cos = is_cos_valid(df)
            bos = is_bos_valid(df)
            btc_ok = is_btc_favorable()

            print(f"[{symbol}] 🔍 Price={price:.6f} | RSI={last_rsi} | MACD={last_macd} | SIGNAL={last_signal} | MA200={last_ma200} | ATR={atr}")
            print(f"↪️ COS={'✅' if cos else '❌'}  BOS={'✅' if bos else '❌'}  BTC={'✅' if btc_ok else '❌'}")

            if not btc_ok or not cos or not bos:
                print(f"[{symbol}] ❌ Signal bloqué (condition non remplie)\n")
                continue

            df.name = symbol
            signal = analyze_signal(df, direction="long")

            if signal:
                print(f"[{symbol}] ✅ Signal analysé avec succès.")
                print(f"📌 Entry={signal['entry']} | SL={signal['sl']} | TP={signal['tp']}")

                signal_id = f"{symbol}-{signal['type']}"
                if signal_id in sent_signals:
                    print(f"[{symbol}] 🔁 Signal déjà envoyé ({signal['type']})\n")
                    continue

                image_path = generate_chart(df, signal)

                message = f"""
{symbol} - Signal {signal['type']} ({signal['direction']})

🔵 Entrée idéale : {signal['entry']:.6f}
🛑 SL : {signal['sl']:.6f}
🎯 TP : {signal['tp']:.6f}
📈 {signal['comment']}
""".strip()

                await bot.send_photo(chat_id=chat_id, photo=open(image_path, 'rb'), caption=message)

                sent_signals[signal_id] = {
                    "entry": signal['entry'],
                    "tp": signal['tp'],
                    "sl": signal['sl'],
                    "sent_at": datetime.utcnow().isoformat()
                }
                with open("sent_signals.json", "w") as f:
                    json.dump(sent_signals, f, indent=2)

                print(f"[{symbol}] ✅ Signal envoyé : {signal['type']}\n")
            else:
                print(f"[{symbol}] ❌ Signal rejeté après analyse (confluence insuffisante)\n")

        except Exception as e:
            print(f"[{symbol}] ⚠️ Erreur : {e}\n")
