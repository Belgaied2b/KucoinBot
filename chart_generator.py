import matplotlib.pyplot as plt
import matplotlib.dates as mdates
import pandas as pd
from datetime import datetime
import os

def generate_chart(df, symbol, ote_zone, fvg_zone, entry, sl, tp, direction):
    df = df.copy().tail(100)
    df['timestamp'] = pd.to_datetime(df['timestamp'], unit='ms')
    df.set_index('timestamp', inplace=True)

    fig, ax = plt.subplots(figsize=(10, 5))
    width = 0.0005 * (df.index[-1] - df.index[0]).total_seconds()

    for i in range(len(df)):
        color = 'green' if df['close'].iloc[i] >= df['open'].iloc[i] else 'red'
        ax.plot([df.index[i], df.index[i]], [df['low'].iloc[i], df['high'].iloc[i]], color=color, linewidth=0.5)
        ax.add_patch(plt.Rectangle(
            (df.index[i], min(df['open'].iloc[i], df['close'].iloc[i])),
            width,
            abs(df['close'].iloc[i] - df['open'].iloc[i]),
            color=color
        ))

    # OTE zone
    ax.axhspan(ote_zone[1], ote_zone[0], color='blue', alpha=0.2, label='OTE')

    # FVG zone
    ax.axhspan(fvg_zone[1], fvg_zone[0], color='orange', alpha=0.2, label='FVG')

    # Entrée, SL, TP
    ax.axhline(entry, color='blue', linestyle='--', linewidth=1)
    ax.axhline(sl, color='red', linestyle='--', linewidth=1)
    ax.axhline(tp, color='green', linestyle='--', linewidth=1)

    # Flèche directionnelle
    y_start = entry
    y_end = tp if direction == "LONG" else sl
    ax.annotate('', xy=(df.index[-1], y_end), xytext=(df.index[-1], y_start),
                arrowprops=dict(facecolor='blue', shrink=0.05, width=2, headwidth=8))

    ax.set_title(f'{symbol} - {direction}')
    ax.legend()
    ax.xaxis.set_major_formatter(mdates.DateFormatter('%m-%d %H:%M'))
    plt.xticks(rotation=45)
    plt.tight_layout()

    # Sauvegarde
    path = f"chart_{symbol.replace('/', '_')}_{direction}.png"
    plt.savefig(path)
    plt.close()
    return path
