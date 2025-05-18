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

    for signal_id, meta in signals.items():
        try:
            symbol, _ = signal_id.split("-")
            df = fetch_klines(symbol, interval='1h', limit=200)
            if df is None or len(df) < 100:
                continue

            df.name = symbol
            new_signal = analyze_signal(df, direction="long")

            # ✅ Filtrer les non-confirmés
            if not new_signal or new_signal["type"] != "CONFIRMÉ":
                print(f"[{symbol}] ❌ Signal supprimé (non confirmé ou invalide)")
                await bot.send_message(chat_id, f"❌ [{symbol}] Signal supprimé – non confirmé ou structure cassée.")
                continue

            # Comparaison des valeurs
            old_entry = float(meta.get("entry", 0))
            old_tp = float(meta.get("tp", 0))
            old_sl = float(meta.get("sl", 0))

            changed = (
                abs(new_signal["entry"] - old_entry) > 0.000001 or
                abs(new_signal["tp"] - old_tp) > 0.000001 or
                abs(new_signal["sl"] - old_sl) > 0.000001
            )

            if changed:
                message = f"""
🔄 [{symbol}] Signal mis à jour

🎯 Nouvelle entrée : {new_signal['entry']:.8f}
📈 TP : {new_signal['tp']:.8f}
🛑 SL : {new_signal['sl']:.8f}
💬 {new_signal['comment']}
""".strip()
                await bot.send_message(chat_id, message)

            updated[signal_id] = {
                "entry": new_signal["entry"],
                "tp": new_signal["tp"],
                "sl": new_signal["sl"],
                "updated_at": datetime.utcnow().isoformat()
            }

        except Exception as e:
            print(f"[{signal_id}] ⚠️ Erreur dans la mise à jour : {e}")

    save_signals(updated)
