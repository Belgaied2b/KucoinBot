import matplotlib.pyplot as plt
import matplotlib.dates as mdates
import pandas as pd
import numpy as np
from datetime import timedelta

def generate_chart(df, symbol, ote_zone, fvg_zone, entry, sl, tp, direction):
    df = df.copy().tail(100)

    if 'timestamp' not in df.columns:
        return None

    df['timestamp'] = pd.to_datetime(df['timestamp'], unit='ms')
    df.set_index('timestamp', inplace=True)

    fig, ax = plt.subplots(figsize=(10, 5))
    candle_width = timedelta(minutes=10)

    for i in range(len(df)):
        color = 'green' if df['close'].iloc[i] >= df['open'].iloc[i] else 'red'
        time = df.index[i]
        open_price = df['open'].iloc[i]
        close_price = df['close'].iloc[i]
        low = df['low'].iloc[i]
        high = df['high'].iloc[i]

        ax.plot([time, time], [low, high], color=color, linewidth=0.5)
        ax.add_patch(plt.Rectangle(
            (time - candle_width / 2, min(open_price, close_price)),
            candle_width,
            abs(close_price - open_price),
            color=color
        ))

    # ✅ Zone OTE
    if not any(np.isnan(ote_zone)):
        ax.axhspan(ote_zone[1], ote_zone[0], color='blue', alpha=0.2, label='OTE')

    # ✅ Zone FVG
    if not any(np.isnan(fvg_zone)):
        ax.axhspan(fvg_zone[1], fvg_zone[0], color='orange', alpha=0.2, label='FVG')

    # ✅ Entry, SL, TP
    ax.axhline(entry, color='blue', linestyle='--', linewidth=1, label='Entry')
    ax.axhline(sl, color='red', linestyle='--', linewidth=1, label='SL')
    ax.axhline(tp, color='green', linestyle='--', linewidth=1, label='TP')

    # ✅ Flèche directionnelle
    try:
        y_start = entry
        y_end = tp if direction.upper() == "LONG" else sl
        if y_end != y_start:
            ax.annotate('', xy=(df.index[-1], y_end), xytext=(df.index[-1], y_start),
                        arrowprops=dict(facecolor='blue', shrink=0.05, width=2, headwidth=8))
    except Exception:
        pass

    ax.set_title(f'{symbol} - {direction.upper()}')
    ax.legend(loc='upper left')
    ax.xaxis.set_major_formatter(mdates.DateFormatter('%m-%d %H:%M'))
    plt.xticks(rotation=45)
    plt.tight_layout()

    path = f"chart_{symbol.replace('/', '_')}_{direction.upper()}.png"
    plt.savefig(path)
    plt.close()
    return path
