from kucoin_utils import fetch_klines
from signal_analysis import analyze_signal
from graph import plot_signal_graph
from io import BytesIO

# Liste des paires à scanner
SYMBOLS = ["BTCUSDTM", "ETHUSDTM"]

async def scan_and_send_signals(bot, chat_id):
    print(f"🔁 Lancement du scan...")

    for symbol in SYMBOLS:
        print(f"\n🔍 Analyse de {symbol}...")

        df_1h = fetch_klines(symbol, interval="1h")
        df_4h = fetch_klines(symbol, interval="4h")

        if df_1h.empty or df_4h.empty:
            print(f"⚠️ {symbol} — Données 1H ou 4H manquantes. Scan ignoré.")
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
