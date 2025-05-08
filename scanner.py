import requests
import json
import os
from kucoin_utils import fetch_klines
from signal_analysis import analyze_signal
from graph import plot_signal_graph
from io import BytesIO

SIGNAL_LOG = "sent_signals.json"

def format_price(value):
    if value >= 100:
        return round(value, 2)
    elif value >= 1:
        return round(value, 4)
    else:
        return round(value, 8)

def get_perp_symbols():
    url = "https://api-futures.kucoin.com/api/v1/contracts/active"
    try:
        response = requests.get(url)
        data = response.json()["data"]
        symbols = [s["symbol"] for s in data if s["symbol"].endswith("USDTM")]
        print(f"🔁 {len(symbols)} PERP détectés")
        return symbols
    except Exception as e:
        print(f"❌ Erreur récupération des PERP : {e}")
        return []

def load_sent_signals():
    if not os.path.exists(SIGNAL_LOG):
        return set()
    with open(SIGNAL_LOG, "r") as f:
        return set(json.load(f))

def save_sent_signal(signal_set):
    with open(SIGNAL_LOG, "w") as f:
        json.dump(list(signal_set), f)

async def scan_and_send_signals(bot, chat_id):
    print(f"🔁 Lancement du scan global PERP KuCoin...")
    symbols = get_perp_symbols()
    sent = load_sent_signals()

    for symbol in symbols:
        print(f"\n🔍 Analyse de {symbol}...")

        df_1h = fetch_klines(symbol, interval="1h")
        df_4h = fetch_klines(symbol, interval="4h")

        if df_1h.empty or df_4h.empty:
            print(f"⚠️ {symbol} — Données manquantes. Ignoré.")
            continue

        for direction in ["long", "short"]:
            status, entry, sl, tp = analyze_signal(df_1h, df_4h, direction)
            signal_id = f"{symbol}_{direction}_{status}"

            if status is None or signal_id in sent:
                continue

            msg = f"{symbol} - Signal {status.upper()} ({direction.upper()})\n"

            if status == "confirmé":
                msg += (
                    f"\n🔵 Entrée idéale : {format_price(entry)}"
                    f"\n🛑 SL : {format_price(sl)}"
                    f"\n🎯 TP : {format_price(tp)}"
                    "\n📈 Signal confirmé avec conditions complètes."
                )
            elif status == "anticipé":
                msg += (
                    "\n📊 RSI + MACD alignés ✅"
                    "\n⏳ Prix pas encore dans la zone OTE + FVG"
                    f"\n🔵 Entrée idéale : {format_price(entry)}"
                    f"\n🛑 SL (prévision) : {format_price(sl)}"
                    f"\n🎯 TP (prévision) : {format_price(tp)}"
                    "\n🧠 Ordre limite possible (à surveiller)"
                )

            # Envoi avec double try/except sécurisé
            fig = plot_signal_graph(df_4h, entry, sl, tp, direction, status=status)
            if fig:
                buf = BytesIO()
                fig.savefig(buf, format='png', dpi=100)
                buf.seek(0)
                try:
                    await bot.send_photo(chat_id=chat_id, photo=buf, caption=msg)
                except Exception as e:
                    print(f"[❌] Erreur envoi image : {e}")
                    try:
                        await bot.send_message(chat_id=chat_id, text=msg + "\n(⚠️ Image non envoyée)")
                    except Exception as e2:
                        print(f"[❌] Erreur fallback texte : {e2}")
            else:
                try:
                    await bot.send_message(chat_id=chat_id, text=msg + "\n(⚠️ Graphique non généré)")
                except Exception as e3:
                    print(f"[❌] Erreur envoi texte brut : {e3}")

            sent.add(signal_id)

    save_sent_signal(sent)
