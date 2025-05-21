# graph.py

import matplotlib.pyplot as plt
import os
import time

def generate_chart(df, signal):
    """
    Trace le cours et positionne Entry / SL / TP,
    sauve la figure et la ferme pour libérer la mémoire.
    """
    # Récupère le symbole depuis df.name (assigné en amont dans scanner.py)
    symbol = getattr(df, 'name', 'UNKNOWN')

    # 1) Création de la figure
    plt.figure()
    plt.plot(df.index, df['close'], label='Close')

    # Entry
    plt.scatter(
        df.index[-1],
        signal['entry'],
        marker='^',
        color='blue',
        label='Entry'
    )

    # SL et TP
    plt.axhline(signal['sl'], linestyle='--', label='SL')
    plt.axhline(signal['tp'], linestyle='--', label='TP')

    # Titre et légende
    plt.title(f"{symbol} – Signal {signal['type']} ({signal['direction']})")
    plt.legend()

    # 2) Sauvegarde
    out_dir = "charts"
    os.makedirs(out_dir, exist_ok=True)
    filename = f"{symbol}_{signal['direction']}_{int(time.time())}.png"
    path = os.path.join(out_dir, filename)
    plt.savefig(path)

    # 3) Fermeture pour éviter les warnings et fuites
    plt.close()

    return path
