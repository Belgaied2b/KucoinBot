# plot_signal.py

import matplotlib.pyplot as plt
import io

def generate_trade_graph(symbol, df, signal):
    df = df.tail(100)
    fig, ax = plt.subplots(figsize=(10, 5))

    # Trace du cours
    ax.plot(df.index, df['close'], color='black', label='Close')
    ax.axhline(signal['entry'], color='blue', linestyle='--', label='Entrée')
    ax.axhline(signal['sl'], color='red', linestyle='--', label='SL')
    ax.axhline(signal['tp'], color='green', linestyle='--', label='TP')

    # Zone OTE
    ote_low, ote_high = signal['ote_zone']
    ax.fill_between(df.index, ote_low, ote_high, color='blue', alpha=0.2, label='Zone OTE')

    # Zone FVG
    fvg_low, fvg_high = signal['fvg_zone']
    ax.fill_between(df.index, fvg_low, fvg_high, color='orange', alpha=0.3, label='Zone FVG')

    ax.set_title(f"Signal {symbol} – OTE+FVG")
    ax.legend()
    ax.grid(True)

    buf = io.BytesIO()
    plt.savefig(buf, format='png')
    buf.seek(0)
    plt.close(fig)
    return buf
