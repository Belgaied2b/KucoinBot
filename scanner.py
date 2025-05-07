import requests
from kucoin_utils import fetch_klines
from signal_analysis import analyze_signal
from graph import plot_signal_graph
from io import BytesIO

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

async def scan_and_send_signals(bot, chat_id):
    print(f"🔁 Lancement du scan global PERP KuCoin...")
    symbols = get_perp_symbols()

    for symbol in symbols:
        print(f"\n🔍 Analyse de {symbol}...")

        df_1h = fetch_klines(symbol, interval="1h")
        df_4h = fetch_klines(symbol, interval="4h")

        if df_1h.empty or df_4h.empty:
            print(f"⚠️ {symbol} — Données manquantes. Ignoré.")
            continue

        for direction in ["long", "short"]:
            status, entry, sl, tp = analyze_signal(df_1h, df_4h, direction)

            if status is None:
                continue

            # 📬 Message Telegram
            msg = f"{symbol} - Signal {status.upper()} ({direction.upper()})\n"

            if status == "confirmé":
                msg += (
                    f"\n🔵 Entrée idéale : {round(entry, 2)}"
                    f"\n🛑 SL : {round(sl, 2)}"
                    f"\n🎯 TP : {round(tp, 2)}"
                    "\n📈 Signal confirmé avec conditions complètes."
                )
            elif status == "anticipé":
                msg += (
                    "\n📊 RSI + MACD alignés ✅"
                    "\n⏳ Prix pas encore dans la zone OTE + FVG"
                )
                if entry and sl and tp:
                    msg += (
                        f"\n🔵 Entrée idéale : {round(entry, 2)}"
                        f"\n🛑 SL (prévision) : {round(sl, 2)}"
                        f"\n🎯 TP (prévision) : {round(tp, 2)}"
                    )
                msg += "\n🧠 Ordre limite possible (à surveiller)"

            # 📉 Génération du graphique
            fig = plot_signal_graph(df_4h, entry or 0, sl, tp if status == "confirmé" else None, direction)
            if fig:
                buf = BytesIO()
                fig.savefig(buf, format='png')
                buf.seek(0)
                await bot.send_photo(chat_id=chat_id, photo=buf, caption=msg)
                print(f"📤 Signal envoyé : {symbol} ({status})")
            else:
                await bot.send_message(chat_id=chat_id, text=msg + " (graphique non généré)")
