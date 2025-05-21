import matplotlib.pyplot as plt
import matplotlib.dates as mdates
import pandas as pd

def generate_chart(df, signal):
    # On ne garde que les 100 dernières barres
    df = df.tail(100).copy()

    # Conversion timestamp → datetime si nécessaire
    if not pd.api.types.is_datetime64_any_dtype(df['timestamp']):
        df['timestamp'] = pd.to_datetime(df['timestamp'], unit='ms')
    df.set_index('timestamp', inplace=True)

    # Récupération du symbole
    symbol = signal.get('symbol', getattr(df, 'name', 'UNKNOWN'))

    # Création figure+axes
    fig, ax = plt.subplots(figsize=(10, 5))

    # Bougies (mèches + corps)
    total_seconds = (df.index[-1] - df.index[0]).total_seconds()
    width = pd.Timedelta(seconds=total_seconds / len(df) * 0.6)
    for i in range(len(df)):
        o, c = df['open'].iloc[i], df['close'].iloc[i]
        low, high = df['low'].iloc[i], df['high'].iloc[i]
        color = 'green' if c >= o else 'red'
        ax.plot([df.index[i]]*2, [low, high], color=color, lw=0.5)
        ax.add_patch(plt.Rectangle(
            (df.index[i], min(o, c)),
            width,
            abs(c - o),
            color=color
        ))

    # Zones OTE & FVG
    if 'ote_zone' in signal:
        ax.axhspan(signal['ote_zone'][1], signal['ote_zone'][0], color='blue',  alpha=0.2, label='OTE')
    if 'fvg_zone' in signal:
        ax.axhspan(signal['fvg_zone'][1], signal['fvg_zone'][0], color='orange',alpha=0.2, label='FVG')

    # MA200
    if 'ma200' in signal:
        ax.plot(df.index, [signal['ma200']]*len(df), linestyle='-', linewidth=1, label='MA200')

    # Entry, SL, TP
    ax.axhline(signal['entry'], color='blue',  ls='--', lw=1, label='Entrée')
    ax.axhline(signal['sl'],    color='red',   ls='--', lw=1, label='SL')
    ax.axhline(signal['tp'],    color='green', ls='--', lw=1, label='TP')

    # Flèche de direction
    y0 = signal['entry']
    y1 = signal['tp']    if signal['direction']=="LONG" else signal['sl']
    ax.annotate('', xy=(df.index[-1], y1), xytext=(df.index[-1], y0),
                arrowprops=dict(facecolor='blue', shrink=0.05, width=2, headwidth=8))

    # Titre, légende, format dates
    ax.set_title(f"{symbol} - {signal['type']} ({signal['direction']})")
    ax.legend()
    ax.xaxis.set_major_formatter(mdates.DateFormatter('%m-%d %H:%M'))
    plt.xticks(rotation=45)
    plt.tight_layout()

    # Sauvegarde + fermeture
    filename = f"chart_{symbol.replace('/', '_')}.png"
    plt.savefig(filename)
    plt.close(fig)

    return filename
