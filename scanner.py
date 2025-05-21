import os
import json
from datetime import datetime
from kucoin_utils import fetch_symbols, fetch_klines

# ─── STUBS ────────────────────────────────────────────────────────────────────
# Ces fonctions sont importées par signal_analysis pour valider COS / BOS / BTC.
# En l’état, elles renvoient True (à remplacer plus tard par ta vraie logique).
def is_cos_valid(df):
    return True

def is_bos_valid(df):
    return True

def is_btc_favorable():
    return True
# ──────────────────────────────────────────────────────────────────────────────

from signal_analysis import analyze_signal
from graph import generate_chart
from config import CHAT_ID

# === Mémoire des signaux envoyés ===
if os.path.exists("sent_signals.json"):
    with open("sent_signals.json", "r") as f:
        sent_signals = json.load(f)
else:
    sent_signals = {}

# === SCAN PRINCIPAL : détection et envoi ===
async def scan_and_send_signals(bot, chat_id):
    print(f"\n🔁 Scan lancé à {datetime.utcnow().strftime('%Y-%m-%d %H:%M:%S')} UTC")
    symbols = fetch_symbols()
    print(f"🔍 Nombre de paires analysées : {len(symbols)}\n")

    for symbol in symbols:
        for direction in ["long", "short"]:
            try:
                signal_id = f"{symbol}-{direction.upper()}"
                if signal_id in sent_signals:
                    continue  # déjà envoyé

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
                    f"🎯 TP : {signal['tp']:.8f}\n"
                    f"📈 {signal['comment']}"
                )
                await bot.send_photo(chat_id=chat_id, photo=open(image_path, 'rb'), caption=message)

                # Mémoriser le signal
                sent_signals[signal_id] = {
                    "entry":    signal["entry"],
                    "tp":       signal["tp"],
                    "sl":       signal["sl"],
                    "sent_at":  datetime.utcnow().isoformat(),
                    "direction":signal["direction"],
                    "symbol":   symbol
                }
                with open("sent_signals.json", "w") as f:
                    json.dump(sent_signals, f, indent=2)

                print(f"[{symbol}] ✅ Signal {direction.upper()} envoyé")

            except Exception as e:
                print(f"[{symbol}] ⚠️ Erreur {direction}: {e}")
