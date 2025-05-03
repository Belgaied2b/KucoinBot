# plot_signal.py

import matplotlib.pyplot as plt
import pandas as pd
import os

def generate_trade_graph(symbol, df, signal):
    if df is None or df.empty or signal is None:
        return None

    plt.figure(figsize=(10, 4))
    plt.plot(df["close"], label="Prix de clôture", linewidth=1.5)

    entry = signal["entry"]
    tp = signal["tp"]
    sl = signal["sl"]

    plt.axhline(entry, color="blue", linestyle="--", linewidth=1, label=f"Entrée ({entry})")
    plt.axhline(tp, color="green", linestyle="--", linewidth=1, label=f"TP ({tp})")
    plt.axhline(sl, color="red", linestyle="--", linewidth=1, label=f"SL ({sl})")

    plt.title(f"Signal sur {symbol}")
    plt.legend()
    plt.tight_layout()

    filename = f"{symbol.replace('/', '_')}_signal.png"
    filepath = os.path.join("/tmp", filename)
    plt.savefig(filepath)
    plt.close()

    return filepath
