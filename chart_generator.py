import matplotlib.pyplot as plt
import matplotlib.patches as patches
import os
from datetime import datetime

def generate_chart(df, symbol, ote_zone, fvg_zones, entry, sl, tp, direction):
    fig, ax = plt.subplots(figsize=(10, 5))
    df_tail = df.tail(100)

    # Tracé des chandeliers simplifiés
    for i in range(len(df_tail)):
        o = df_tail["open"].iloc[i]
        h = df_tail["high"].iloc[i]
        l = df_tail["low"].iloc[i]
        c = df_tail["close"].iloc[i]
        color = "green" if c >= o else "red"
        ax.plot([i, i], [l, h], color=color, linewidth=1)
        ax.plot([i, i], [o, c], color=color, linewidth=4)

    # Zone OTE (rectangle bleu)
    if ote_zone:
        ax.add_patch(
            patches.Rectangle(
                (90, ote_zone[0]),
                10,
                ote_zone[1] - ote_zone[0],
                linewidth=1,
                edgecolor='blue',
                facecolor='blue',
                alpha=0.2,
                label="OTE"
            )
        )

    # Zones FVG (rectangle gris)
    for fvg in fvg_zones:
        ax.add_patch(
            patches.Rectangle(
                (90, fvg[0]),
                10,
                fvg[1] - fvg[0],
                linewidth=1,
                edgecolor='gray',
                facecolor='gray',
                alpha=0.2,
                label="FVG"
            )
        )

    # SL, TP, Entry
    if entry:
        ax.axhline(entry, color="orange", linestyle="--", label="Entrée")
    if sl:
        ax.axhline(sl, color="red", linestyle="--", label="SL")
    if tp:
        ax.axhline(tp, color="green", linestyle="--", label="TP")

    # Direction (flèche)
    y_pos = entry or df_tail["close"].iloc[-1]
    if direction == "long":
        ax.annotate('⬆️ LONG', xy=(95, y_pos), xytext=(95, y_pos + 0.02 * y_pos), fontsize=12)
    else:
        ax.annotate('⬇️ SHORT', xy=(95, y_pos), xytext=(95, y_pos - 0.02 * y_pos), fontsize=12)

    ax.set_title(f"{symbol} – Signal {direction.upper()}")
    ax.set_xlim(85, 100)
    ax.grid(True)

    # Sauvegarde
    now = datetime.now().strftime("%Y%m%d_%H%M%S")
    filename = f"charts/{symbol}_{direction}_{now}.png"
    os.makedirs("charts", exist_ok=True)
    plt.savefig(filename, bbox_inches='tight')
    plt.close()

    return filename
