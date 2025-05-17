import matplotlib.pyplot as plt
import matplotlib.dates as mdates
import pandas as pd

def generate_chart(df, signal):
    df = df.tail(100).copy()
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

    # Zones OTE & FVG (si elles existent dans le signal)
    if 'ote_zone' in signal:
        ax.axhspan(signal['ote_zone'][1], signal['ote_zone'][0], color='blue', alpha=0.2, label='OTE')
    if 'fvg_zone' in signal:
        ax.axhspan(signal['fvg_zone'][1], signal['fvg_zone'][0], color='orange', alpha=0.2, label='FVG')

    # Niveaux SL, TP, Entry
    ax.axhline(signal['entry'], color='blue', linestyle='--', linewidth=1, label='Entrée')
    ax.axhline(signal['sl'], color='red', linestyle='--', linewidth=1, label='SL')
    ax.axhline(signal['tp'], color='green', linestyle='--', linewidth=1, label='TP')

    # Direction visuelle
    y_start = signal['entry']
    y_end = signal['tp'] if signal['direction'] == "LONG" else signal['sl']
    ax.annotate('', xy=(df.index[-1], y_end), xytext=(df.index[-1], y_start),
                arrowprops=dict(facecolor='blue', shrink=0.05, width=2, headwidth=8))

    ax.set_title(f"{signal['symbol']} - {signal['type']} ({signal['direction']})")
    ax.legend()
    ax.xaxis.set_major_formatter(mdates.DateFormatter('%m-%d %H:%M'))
    plt.xticks(rotation=45)
    plt.tight_layout()

    filename = f"chart_{signal['symbol'].replace('/', '_')}.png"
    plt.savefig(filename)
    plt.close()
    return filename
