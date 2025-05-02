# plot_signal.py

import matplotlib.pyplot as plt

def generate_trade_chart(df, symbol, entry, sl, tp, filename):
    plt.figure(figsize=(10, 5))
    plt.plot(df["timestamp"], df["close"], label="Prix")
    plt.axhline(entry, color="blue", linestyle="--", label=f"Entrée {entry:.4f}")
    plt.axhline(sl, color="red", linestyle="--", label=f"SL {sl:.4f}")
    plt.axhline(tp, color="green", linestyle="--", label=f"TP {tp:.4f}")
    plt.title(f"Signal {symbol}")
    plt.xlabel("Temps")
    plt.ylabel("Prix")
    plt.legend()
    plt.tight_layout()
    plt.savefig(filename)
    plt.close()
