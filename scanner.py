import os, json
from datetime import datetime
from kucoin_utils import fetch_symbols, fetch_klines
from signal_analysis import analyze_signal
from graph import generate_chart
from indicators import compute_rsi as rsi, compute_macd as macd
from config import CHAT_ID

if os.path.exists("sent_signals.json"):
    with open("sent_signals.json", "r") as f:
        sent_signals = json.load(f)
else:
    sent_signals = {}

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
        df['macd'], df['signal'] = macd(df['close'])
        return df['rsi'].iloc[-1] > 50 and df['macd'].iloc[-1] > df['signal'].iloc[-1]
    except:
        return True

# 🔁 Vérifie les signaux envoyés, supprime ceux invalides
async def update_existing_signals(bot):
    updated_signals = {}
    for signal_id, data in sent_signals.items():
        try:
            symbol, direction = signal_id.split('-')
            df = fetch_klines(symbol)
            df.name = symbol
            signal = analyze_signal(df, direction=direction.lower())

            if not signal:
                # ❌ Signal plus valide → supprimer et notifier
                print(f"[{symbol}] ❌ Signal {direction} invalidé – supprimé")
                message = f"⚠️ Signal {symbol} ({direction.upper()}) retiré : structure non valide."
                await bot.send_message(chat_id=CHAT_ID, text=message)
                continue

            # ✅ Signal toujours valide → on le garde sans rien changer
            updated_signals[signal_id] = data

        except Exception as e:
            print(f"[{signal_id}] ⚠️ Erreur update: {e}")

    with open("sent_signals.json", "w") as f:
        json.dump(updated_signals, f, indent=2)

# 📤 Scan principal : détection et envoi des nouveaux signaux
async def scan_and_send_signals(bot, chat_id):
    print(f"\n🔁 Scan déclenché à {datetime.utcnow().strftime('%Y-%m-%d %H:%M:%S')} UTC")
    await update_existing_signals(bot)

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

                signal_id = f"{symbol}-{direction.upper()}"
                if signal_id in sent_signals:
                    continue  # déjà envoyé

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
                    "sent_at": datetime.utcnow().isoformat(),
                    "direction": signal['direction'],
                    "symbol": symbol
                }

                with open("sent_signals.json", "w") as f:
                    json.dump(sent_signals, f, indent=2)

                print(f"[{symbol}] ✅ Signal {direction.upper()} envoyé")

            except Exception as e:
                print(f"[{symbol}] ⚠️ Erreur {direction}: {e}")
