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

            msg = f"{symbol} - Signal {status.upper()} ({direction})"

            if status == "confirmé":
                fig = plot_signal_graph(df_4h, entry, sl, tp, direction)
                if fig:
                    buf = BytesIO()
                    fig.savefig(buf, format='png')
                    buf.seek(0)
                    await bot.send_photo(chat_id=chat_id, photo=buf, caption=msg)
                    print(f"📤 Signal envoyé : {msg}")
                else:
                    await bot.send_message(chat_id=chat_id, text=msg + " (graphique non généré)")
            else:
                await bot.send_message(chat_id=chat_id, text=msg)
                print(f"📤 Signal anticipé envoyé : {msg}")
