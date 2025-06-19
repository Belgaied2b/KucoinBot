import json
import os
import time
from datetime import datetime
import traceback
import requests
import pandas as pd
from kucoin_utils import fetch_all_symbols, fetch_klines
from signal_analysis import analyze_signal
from config import TOKEN, CHAT_ID
from telegram import Bot

bot = Bot(token=TOKEN)

# 🔁 Envoi Telegram
async def send_signal_to_telegram(signal):
    rejected = signal.get("rejetes", [])
    tolerated = signal.get("toleres", [])

    msg_rejected = f"❌ Rejetés : {', '.join(rejected)}" if rejected else ""
    msg_tolerated = f"⚠️ Tolérés : {', '.join(tolerated)}" if tolerated else ""

    message = (
        f"📉 {signal['symbol']} - Signal CONFIRMÉ ({signal['direction']})\n\n"
        f"🎯 Entry : {signal['entry']:.4f}\n"
        f"🛑 SL    : {signal['sl']:.4f}\n"
        f"🎯 TP1   : {signal['tp1']:.4f}\n"
        f"🎯 TP2   : {signal['tp2']:.4f}\n"
        f"📈 R:R1  : {signal['rr1']}\n"
        f"📈 R:R2  : {signal['rr2']}\n"
        f"🧠 Score : {signal.get('score', '?')}/10\n"
        f"{signal.get('comment', '')}\n"
        f"{msg_tolerated}\n"
        f"{msg_rejected}"
    )

    print(f"[{signal['symbol']}] 📤 Envoi Telegram en cours...")
    await bot.send_message(chat_id=CHAT_ID, text=message.strip())

# 📂 Gestion des doublons
sent_signals = {}
if os.path.exists("sent_signals.json"):
    try:
        with open("sent_signals.json", "r") as f:
            sent_signals = json.load(f)
        print("📂 sent_signals.json chargé")
    except Exception as e:
        print("⚠️ Erreur lecture sent_signals.json :", e)

# ✅ Fonction robuste CoinGecko
def get_chart(url):
    try:
        time.sleep(1.2)
        r = requests.get(url)
        r.raise_for_status()
        data = r.json()

        if "prices" not in data:
            raise ValueError("⚠️ 'prices' absent de la réponse")

        timestamps = [x[0] for x in data["prices"]]
        closes = [x[1] for x in data["prices"]]

        if "total_volumes" in data and len(data["total_volumes"]) == len(timestamps):
            volumes = [x[1] for x in data["total_volumes"]]
        else:
            volumes = [0 for _ in timestamps]

        df = pd.DataFrame({
            "timestamp": timestamps,
            "close": closes,
            "high": [c * 1.01 for c in closes],
            "low": [c * 0.99 for c in closes],
            "open": closes,
            "volume": volumes
        })

        return df

    except Exception as e:
        print(f"⚠️ Erreur get_chart ({url}): {e}")
        return None

# 📊 Chargement macro BTC / TOTAL / BTC.D
def fetch_macro_df():
    btc_df = get_chart("https://api.coingecko.com/api/v3/coins/bitcoin/market_chart?vs_currency=usd&days=30")
    if btc_df is None:
        raise ValueError("Impossible de charger les données BTC")

    btc_d_df = get_chart("https://api.coingecko.com/api/v3/coins/bitcoin/market_chart?vs_currency=usd&days=30")
    if btc_d_df is None:
        raise ValueError("Impossible de charger les données BTC.D")

    try:
        global_response = requests.get("https://api.coingecko.com/api/v3/global")
        global_data = global_response.json()

        if "data" not in global_data or "market_cap_percentage" not in global_data["data"]:
            raise ValueError("Champ 'data' manquant dans la réponse CoinGecko")

        btc_dominance = global_data["data"]["market_cap_percentage"]["btc"] / 100
        total_market_cap = btc_df["close"] / btc_dominance

        total_df = btc_df.copy()
        total_df["close"] = total_market_cap
        total_df["high"] = total_market_cap * 1.01
        total_df["low"] = total_market_cap * 0.99
        total_df["open"] = total_market_cap
    except Exception as e:
        raise ValueError(f"Erreur parsing global_data : {e}")

    return btc_df, total_df, btc_d_df

# 🔍 Scan principal
async def scan_and_send_signals():
    print(f"🔁 Scan lancé à {datetime.utcnow().strftime('%Y-%m-%d %H:%M:%S')} UTC\n")
    all_symbols = fetch_all_symbols()

    try:
        btc_df, total_df, btc_d_df = fetch_macro_df()
    except Exception as e:
        print(f"⚠️ Erreur macro fetch : {e}")
        return

    for symbol in all_symbols:
        if not symbol.endswith("USDTM"):
            continue

        try:
            df = fetch_klines(symbol)
            if df is None or df.empty or 'timestamp' not in df.columns or len(df) < 50:
                print(f"[{symbol}] ⚠️ Données insuffisantes ou format invalide, ignoré")
                continue

            for direction in ["long", "short"]:
                print(f"[{symbol}] ➡️ Analyse {direction.upper()}")

                df_copy = df.copy()
                df_copy.name = symbol  # ✅ Corrige "Unknown" dans tous les cas

                signal = analyze_signal(
                    df_copy,
                    direction=direction,
                    btc_df=btc_df,
                    total_df=total_df,
                    btc_d_df=btc_d_df
                )

                if signal:
                    suffix = "TOLÉRÉ" if signal.get("tolere_ote") else "CONFIRMÉ"
                    signal_id = f"{symbol}-{direction.upper()}-{suffix}"

                    if signal_id in sent_signals:
                        print(f"[{symbol}] 🔁 Signal déjà envoyé ({direction.upper()}-{suffix}), ignoré")
                        continue

                    score = signal.get("score", "?")
                    print(f"[{symbol}] ✅ Nouveau signal accepté : {direction.upper()} ({suffix}) | Score pondéré : {score}/10")
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
            print(f"[{symbol}] ⚠️ Erreur analyse signal ({direction.upper()}) : {e}")
            traceback.print_exc()
