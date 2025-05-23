import os
import json
from datetime import datetime
from kucoin_utils import fetch_symbols, fetch_klines

# Stubs avec 2 arguments (corrigé)

def is_cos_valid(df, direction):
    Détection simplifiée du COS (Change of Structure)
Retourne True si un swing inverse s'est formé récemment.
    window = 5
    if direction == "long":
        last_pivot_low = df['low'].rolling(window).min().iloc[-1]
        return df['close'].iloc[-1] > last_pivot_low * 1.02
    else:
        last_pivot_high = df['high'].rolling(window).max().iloc[-1]
        return df['close'].iloc[-1] < last_pivot_high * 0.98
    


def is_bos_valid(df, direction):
    "
    Détection simplifiée du BOS (Break of Structure)
    "
    highs = df['high'].rolling(5).max()
    lows = df['low'].rolling(5).min()
    if direction == "long":
        return df['close'].iloc[-1] > highs.iloc[-5]
    else:
        return df['close'].iloc[-1] < lows.iloc[-5]
    

def is_btc_favorable():
    return True

from signal_analysis import analyze_signal
from graph import generate_chart
from config import CHAT_ID

# Mémoire des signaux envoyés
if os.path.exists("sent_signals.json"):
    with open("sent_signals.json", "r") as f:
        sent_signals = json.load(f)
else:
    sent_signals = {}

async def scan_and_send_signals(bot, chat_id):
    print(f"\n🔁 Scan lancé à {datetime.utcnow().strftime('%Y-%m-%d %H:%M:%S')} UTC")
    symbols = fetch_symbols()
    print(f"🔍 Nombre de paires analysées : {len(symbols)}\n")

    for symbol in symbols:
        for direction in ["long", "short"]:
            try:
                signal_id = f"{symbol}-{direction.upper()}"
                if signal_id in sent_signals:
                    continue

                df = fetch_klines(symbol)
                if df is None or len(df) < 100:
                    continue

                df.name = symbol
                signal = analyze_signal(df, direction=direction)
                if not signal or signal["type"] != "CONFIRMÉ":
                    continue

                image_path = generate_chart(df, signal)
                message = (
                    f"{symbol} - Signal {signal['type']} ({signal['direction']})\n\n"
                    f"🔵 Entrée idéale : {signal['entry']:.8f}\n"
                    f"🛑 SL : {signal['sl']:.8f}\n"
                    f"🎯 TP1 : {signal['tp1']:.8f}\n"
                    f"🚀 TP2 : {signal['tp2']:.8f}\n"
                    f"📈 R:R1 = {signal['rr1']} / R:R2 = {signal['rr2']}\n"
                    f"{signal['comment']}"
                )
                await bot.send_photo(chat_id=chat_id, photo=open(image_path, 'rb'), caption=message)

                sent_signals[signal_id] = {
                    "entry": signal["entry"],
                    "tp":    signal["tp1"],
                    "sl":    signal["sl"],
                    "sent_at": datetime.utcnow().isoformat(),
                    "direction": signal["direction"],
                    "symbol": symbol
                }
                with open("sent_signals.json", "w") as f:
                    json.dump(sent_signals, f, indent=2)

                print(f"[{symbol}] ✅ Signal {direction.upper()} envoyé")

            except Exception as e:
                print(f"[{symbol}] ⚠️ Erreur {direction}: {e}")
