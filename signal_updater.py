# signal_updater.py

import json
import os
from datetime import datetime
from kucoin_utils import fetch_klines
from signal_analysis import analyze_signal
from telegram import Bot

SIGNALS_PATH = "sent_signals.json"

def load_signals():
    if not os.path.exists(SIGNALS_PATH):
        return {}
    with open(SIGNALS_PATH, "r") as f:
        return json.load(f)

def save_signals(signals):
    with open(SIGNALS_PATH, "w") as f:
        json.dump(signals, f, indent=2)

async def check_active_signals_and_update(bot: Bot, chat_id: int):
    signals = load_signals()
    updated = {}

    for signal_id, timestamp in signals.items():
        try:
            symbol, _ = signal_id.split("-")
            df = fetch_klines(symbol, interval='1h', limit=200)
            if df is None or len(df) < 100:
                continue

            df.name = symbol
            new_signal = analyze_signal(df, direction="long")

            if not new_signal:
                print(f"[{symbol}] ❌ Signal devenu invalide (structure cassée ou SL touché)")
                await bot.send_message(chat_id, f"❌ [{symbol}] Signal annulé – structure non valide ou SL touché.")
                continue

            # Identique ? => rien à faire
            old_entry = round(float(signals[signal_id].get("entry", 0)), 6)
            old_tp = round(float(signals[signal_id].get("tp", 0)), 6)
            old_sl = round(float(signals[signal_id].get("sl", 0)), 6)

            changed = (
                abs(new_signal["entry"] - old_entry) > 0.001 or
                abs(new_signal["tp"] - old_tp) > 0.001 or
                abs(new_signal["sl"] - old_sl) > 0.001
            )

            if changed:
                message = f"""
🔄 [{symbol}] Signal mis à jour

🎯 Nouvelle entrée : {new_signal['entry']}
📈 TP : {new_signal['tp']}
🛑 SL : {new_signal['sl']}
💬 {new_signal['comment']}
""".strip()
                await bot.send_message(chat_id, message)

                updated[signal_id] = {
                    "entry": new_signal["entry"],
                    "tp": new_signal["tp"],
                    "sl": new_signal["sl"],
                    "updated_at": datetime.utcnow().isoformat()
                }

            else:
                updated[signal_id] = signals[signal_id]

        except Exception as e:
            print(f"[{signal_id}] ⚠️ Erreur dans la mise à jour : {e}")

    save_signals(updated)
