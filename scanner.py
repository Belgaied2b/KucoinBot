# scanner.py

import os
import json
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

# ✅ COS adaptatif
def is_cos_valid(df):
    if len(df) < 50:
        return False
    recent_zone = df[-20:]
    previous_zone = df[-40:-20]
    prev_high = previous_zone['high'].max()
    last_high = recent_zone['high'].iloc[-1]
    return last_high > prev_high

# ✅ BOS (Break of Structure)
def is_bos_valid(df):
    recent_high = df['high'].iloc[-5:-1].max()
    current_close = df['close'].iloc[-1]
    return current_close > recent_high

# ✅ Tendance du BTC
def is_btc_favorable():
    try:
        df = fetch_klines('BTC/USDT:USDT', interval='1h', limit=100)
        df['rsi'] = rsi(df['close'])
        df['macd'], df['signal'] = macd(df['close'])
        return df['rsi'].iloc[-1] > 50 and df['macd'].iloc[-1] > df['signal'].iloc[-1]
    except:
        return True

# ✅ Scan complet
async def scan_and_send_signals(bot, chat_id):
    print(f"\n--- Scan lancé à {datetime.utcnow().strftime('%Y-%m-%d %H:%M:%S')} UTC ---")

    symbols = fetch_symbols()
    print(f"🔍 Nombre de paires analysées : {len(symbols)}\n")

    for symbol in symbols:
        try:
            df = fetch_klines(symbol, interval='1h', limit=200)
            if df is None or len(df) < 100:
                continue

            df.name = symbol
            signal = analyze_signal(df, direction="long")

            if not signal or signal["type"] != "CONFIRMÉ":
                continue

            signal["symbol"] = symbol
            signal_id = f"{symbol}-{signal['type']}"
            if signal_id in sent_signals:
                continue

            image_path = generate_chart(df, signal)

            message = f"""
{symbol} - Signal {signal['type']} ({signal['direction']})

🔵 Entrée idéale : {signal['entry']:.8f}
🛑 SL : {signal['sl']:.8f}
🎯 TP : {signal['tp']:.8f}
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

        except Exception as e:
            print(f"[{symbol}] ⚠️ Erreur : {e}\n")
