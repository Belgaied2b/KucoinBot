### scanner.py
from kucoin_utils import fetch_klines
from signal_analysis import analyze_signal
from graph import plot_signal_graph
from io import BytesIO

SYMBOLS = ["BTCUSDTM", "ETHUSDTM"]

async def scan_and_send_signals(bot, chat_id):
    for symbol in SYMBOLS:
        df_1h = fetch_klines(symbol, interval="1h")
        df_4h = fetch_klines(symbol, interval="4h")
        for direction in ["long", "short"]:
            status, entry, sl, tp = analyze_signal(df_1h, df_4h, direction)
            if status:
                msg = f"{symbol} - Signal {status.upper()} ({direction})"
                if status == "confirmé":
                    fig = plot_signal_graph(df_4h, entry, sl, tp, direction)
                    buf = BytesIO()
                    fig.savefig(buf, format='png')
                    buf.seek(0)
                    await bot.send_photo(chat_id=chat_id, photo=buf, caption=msg)
                else:
                    await bot.send_message(chat_id=chat_id, text=msg)

