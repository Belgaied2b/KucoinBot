import json
import os
from datetime import datetime
from kucoin_utils import fetch_all_symbols, fetch_klines
from signal_analysis import analyze_signal
from structure_utils import is_cos_valid, is_bos_valid, is_btc_favorable
from config import TOKEN, CHAT_ID
from telegram import Bot
import traceback

bot = Bot(token=TOKEN)

async def send_signal_to_telegram(signal):
    message = (
        f"📉 {signal['symbol']} - Signal CONFIRMÉ ({signal['direction']})\n\n"
        f"🎯 Entry : {signal['entry']:.4f}\n"
        f"🛑 SL    : {signal['sl']:.4f}\n"
        f"🎯 TP1   : {signal['tp1']:.4f}\n"
        f"🎯 TP2   : {signal['tp2']:.4f}\n"
        f"📈 R:R1  : {signal['rr1']}\n"
        f"📈 R:R2  : {signal['rr2']}\n"
        f"🧠 Score : {signal.get('score', '?')}/10\n"
        f"{signal.get('comment', '')}"
    )
    print(f"[{signal['symbol']}] 📤 Envoi Telegram en cours...")
    await bot.send_message(chat_id=CHAT_ID, text=message)

sent_signals = {}
if os.path.exists("sent_signals.json"):
    try:
        with open("sent_signals.json", "r") as f:
            sent_signals = json.load(f)
        print("📂 sent_signals.json chargé :")
        print(json.dumps(sent_signals, indent=2))
    except Exception as e:
        print("⚠️ Erreur lecture sent_signals.json :", e)

async def scan_and_send_signals():
    print(f"🔁 Scan lancé à {datetime.utcnow().strftime('%Y-%m-%d %H:%M:%S')} UTC\n")
    all_symbols = fetch_all_symbols()

    for symbol in all_symbols:
        if not symbol.endswith("USDTM"):
            continue

        try:
            df = fetch_klines(symbol)
            df.name = symbol

            for direction in ["long", "short"]:
                print(f"[{symbol}] ➡️ Analyse {direction.upper()}")
                signal = analyze_signal(df, direction=direction)

                if signal:
                    signal_id = f"{symbol}-{direction.upper()}"
                    if signal_id in sent_signals:
                        print(f"[{symbol}] 🔁 Signal déjà envoyé ({direction.upper()}), ignoré")
                        continue

                    print(f"[{symbol}] ✅ Nouveau signal accepté : {direction.upper()}")
                    await send_signal_to_telegram(signal)

                    sent_signals[signal_id] = {
                        "entry": signal["entry"],
                        "tp": signal["tp1"],
                        "sl": signal["sl"],
                        "sent_at": datetime.utcnow().isoformat(),
                        "direction": signal["direction"],
                        "symbol": symbol
                    }

                    with open("sent_signals.json", "w") as f:
                        json.dump(sent_signals, f, indent=2)

        except Exception as e:
            print(f"[{symbol}] ⚠️ Erreur {direction}: {e}")
            traceback.print_exc()
