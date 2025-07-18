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
    comment = signal.get("comment", "").strip()

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
        f"{comment}\n"
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

# ✅ Requête CoinGecko robuste
def get_chart(url):
    headers = {"User-Agent": "Mozilla/5.0"}
    try:
        time.sleep(1.5)
        r = requests.get(url, headers=headers)
        r.raise_for_status()
        data = r.json()

        if "prices" not in data:
            raise ValueError("⚠️ 'prices' absent de la réponse")

        timestamps = [x[0] for x in data["prices"]]
        closes = [x[1] for x in data["prices"]]
        volumes = (
            [x[1] for x in data["total_volumes"]]
            if "total_volumes" in data and len(data["total_volumes"]) == len(timestamps)
            else [0 for _ in timestamps]
        )

        df = pd.DataFrame({
            "timestamp": timestamps,
            "close": closes,
            "high": [c * 1.01 for c in closes],
            "low": [c * 0.99 for c in closes],
            "open": closes,
            "volume": volumes
        })

        df["timestamp"] = pd.to_datetime(df["timestamp"], unit="ms")
        df = df[["timestamp", "open", "high", "low", "close", "volume"]]
        df.set_index("timestamp", inplace=False)
        return df

    except Exception as e:
        print(f"⚠️ Erreur get_chart ({url}): {e}")
        return None

# 📊 Chargement des données macro
def fetch_macro_df():
    headers = {"User-Agent": "Mozilla/5.0"}

    btc_df = get_chart("https://api.coingecko.com/api/v3/coins/bitcoin/market_chart?vs_currency=usd&days=30")
    if btc_df is None:
        raise ValueError("Impossible de charger les données BTC")

    try:
        time.sleep(1.5)
        response = requests.get("https://api.coingecko.com/api/v3/global", headers=headers)
        response.raise_for_status()
        global_data = response.json()

        if "data" not in global_data or "market_cap_percentage" not in global_data["data"]:
            raise ValueError("Champ 'data' manquant dans la réponse CoinGecko")

        btc_dominance = global_data["data"]["market_cap_percentage"]["btc"] / 100
        total_market_cap = btc_df["close"] / btc_dominance

        total_df = btc_df.copy()
        total_df["close"] = total_market_cap
        total_df["high"] = total_market_cap * 1.01
        total_df["low"] = total_market_cap * 0.99
        total_df["open"] = total_market_cap

        btc_d_df = btc_df.copy()
        btc_d_df["close"] = btc_dominance

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
            if df is None or df.empty or 'timestamp' not in df.columns:
                print(f"[{symbol}] ⚠️ Données invalides ou vides, ignoré")
                continue

            for direction in ["long", "short"]:
                print(f"[{symbol}] ➡️ Analyse {direction.upper()}")

                df_copy = df.copy()
                df_copy.name = symbol

                signal = analyze_signal(
                    df_copy,
                    symbol=symbol,
                    direction=direction,
                    btc_df=btc_df,
                    total_df=total_df,
                    btc_d_df=btc_d_df
                )

                score = signal.get("score", 0)
                rejected = signal.get("rejetes", [])
                tolerated = signal.get("toleres", [])
                comment = signal.get("comment", "")

                if signal.get("valid"):
                    suffix = "TOLÉRÉ" if signal.get("tolere_ote") else "CONFIRMÉ"
                    signal_id = f"{symbol}-{direction.upper()}-{suffix}"

                    if signal_id in sent_signals:
                        print(f"[{symbol}] 🔁 Signal déjà envoyé ({direction.upper()}-{suffix}), ignoré")
                        continue

                    print(f"[{symbol}] ✅ Nouveau signal accepté : {direction.upper()} ({suffix})")
                    print(f"   🧠 Score     : {score}/10")
                    if tolerated:
                        print(f"   ⚠️ Tolérés   : {', '.join(tolerated)}")
                    if rejected:
                        print(f"   ❌ Rejetés   : {', '.join(rejected)}")
                    if comment:
                        print(f"   💬 Commentaire : {comment.strip()}")
                    print("-" * 60)

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

                else:
                    print(f"[{symbol}] ❌ Aucun signal détecté ({direction.upper()})")
                    print(f"   🧠 Score     : {score}/10")
                    if tolerated:
                        print(f"   ⚠️ Tolérés   : {', '.join(tolerated)}")
                    if rejected:
                        print(f"   ❌ Rejetés   : {', '.join(rejected)}")
                    if comment:
                        print(f"   💬 Commentaire : {comment.strip()}")
                    print("-" * 60)

        except Exception as e:
            print(f"[{symbol}] ⚠️ Erreur analyse signal : {e}")
            traceback.print_exc()
