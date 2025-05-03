import matplotlib.pyplot as plt
from matplotlib.dates import DateFormatter, AutoDateLocator
from io import BytesIO

def generate_trade_graph(symbol, df, signal):
    """
    Génère un graphique 4H :
      - Prix de clôture
      - Lignes Entrée (bleu), TP (vert), SL (rouge)
      - Annotation du prix actuel
      - Grille et date-format améliorés
    """
    fig, ax = plt.subplots(figsize=(10, 5))

    # Trace du prix de clôture (float)
    ax.plot(df.index, df["close"], label="Prix de clôture")

    # Récupère les prix convertis en float
    entry_price = float(signal["entry"])
    tp_price    = float(signal["tp"])
    sl_price    = float(signal["sl"])

    # Lignes horizontales
    ax.axhline(entry_price, color="blue", linestyle="--", label=f"Entrée {signal['entry']}")
    ax.axhline(tp_price,    color="green", linestyle="--", label=f"TP     {signal['tp']}")
    ax.axhline(sl_price,    color="red",   linestyle="--", label=f"SL     {signal['sl']}")

    # Annotation du cours actuel
    current_price = df["close"].iloc[-1]
    current_time  = df.index[-1]
    ax.annotate(
        f"{current_price:.4f}",
        xy=(current_time, current_price),
        xytext=(10, 10),
        textcoords="offset points",
        arrowprops=dict(arrowstyle="->", lw=1)
    )

    # Grille et labels
    ax.grid(True, which="both", linestyle="--", linewidth=0.5)
    ax.set_title(f"{symbol} – Signal {signal['side']}", fontsize=14, pad=10)
    ax.set_xlabel("Date", fontsize=12)
    ax.set_ylabel("Prix", fontsize=12)

    # Formatage des dates
    locator   = AutoDateLocator()
    formatter = DateFormatter("%Y-%m-%d\n%H:%M")
    ax.xaxis.set_major_locator(locator)
    ax.xaxis.set_major_formatter(formatter)
    fig.autofmt_xdate()

    ax.legend(loc="upper left")

    # Envoie dans un buffer
    buf = BytesIO()
    fig.savefig(buf, format="png", bbox_inches="tight")
    buf.seek(0)
    plt.close(fig)
    return buf
