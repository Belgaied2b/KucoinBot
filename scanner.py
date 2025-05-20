import os
from datetime import datetime
from kucoin_utils import fetch_symbols, fetch_klines
from signal_analysis import analyze_signal
from graph import generate_chart
from indicators import compute_rsi as rsi, compute_macd as macd

# === CONFIG ===
from config import CHAT_ID


def is_cos_valid(df):
    recent = df[-20:]
    return recent['low'].iloc[-1] > recent['low'].min()


def is_bos_valid(df):
    recent_high = df['high'].iloc[-5:-1].max()
    current_close = df['close'].iloc[-1]
    return current_close > recent_high


def is_btc_favorable():
    try:
        df = fetch_klines('BTC/USDT:USDT', interval='1h', limit=100)
        df['rsi'] = rsi(df['close'])
        df['macd'], df['signal'] = compute_macd(df['close'])
        return df['rsi'].iloc[-1] > 50 and df['macd'].iloc[-1] > df['signal'].iloc[-1]
    except:
        return True


# === SCAN PRINCIPAL : analyse & envoi des signaux ===
async def scan_and_send_signals(bot, chat_id):
    print(f"\n🔁 Scan lancé à {datetime.utcnow().strftime('%Y-%m-%d %H:%M:%S')} UTC")
    symbols = fetch_symbols()
    print(f"🔍 Nombre de paires analysées : {len(symbols)}\n")

    for symbol in symbols:
        for direction in ["long", "short"]:
            try:
                df = fetch_klines(symbol)
                if df is None or len(df) < 100:
                    continue

                df.name = symbol
                signal = analyze_signal(df, direction=direction)
                if not signal or signal["type"] != "CONFIRMÉ":
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
                print(f"[{symbol}] ✅ Signal {direction.upper()} envoyé")

            except Exception as e:
                print(f"[{symbol}] ⚠️ Erreur {direction}: {e}")
