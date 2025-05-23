import json
import os
from datetime import datetime
from kucoin_utils import fetch_all_symbols, fetch_klines
from signal_analysis import analyze_signal
from telegram_bot import send_signal_to_telegram

# ✅ Mémoire des signaux déjà envoyés
if os.path.exists("sent_signals.json"):
    with open("sent_signals.json", "r") as f:
        sent_signals = json.load(f)
else:
    sent_signals = {}

def is_cos_valid(df, direction):
    """
    Détection simplifiée du COS (Change of Structure)
    Retourne True si un swing inverse s'est formé récemment.
    """
    window = 5
    if direction == "long":
        last_pivot_low = df['low'].rolling(window).min().iloc[-1]
        return df['close'].iloc[-1] > last_pivot_low * 1.02
    else:
        last_pivot_high = df['high'].rolling(window).max().iloc[-1]
        return df['close'].iloc[-1] < last_pivot_high * 0.98

def is_bos_valid(df, direction):
    """
    Détection simplifiée du BOS (Break of Structure)
    Retourne True si le dernier prix casse le plus haut ou plus bas récent.
    """
    highs = df['high'].rolling(5).max()
    lows = df['low'].rolling(5).min()
    if direction == "long":
        return df['close'].iloc[-1] > highs.iloc[-5]
    else:
        return df['close'].iloc[-1] < lows.iloc[-5]

def is_btc_favorable():
    # Simule une tendance BTC haussière (à adapter si tu veux un vrai fetch BTC)
    return True

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
                signal = analyze_signal(df.copy(), direction=direction)

                if signal:
                    signal_id = f"{symbol}-{direction.upper()}"
                    if signal_id in sent_signals:
                        continue

                    send_signal_to_telegram(signal)
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
