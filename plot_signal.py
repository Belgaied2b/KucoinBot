import matplotlib.pyplot as plt
import os

def generate_trade_graph(symbol, df, entry, tp, sl):
    plt.figure()
    df["close"].plot(label="Close Price", linewidth=1.5)
    plt.axhline(entry, color='blue', linestyle='--', label='Entrée')
    plt.axhline(tp, color='green', linestyle='--', label='TP')
    plt.axhline(sl, color='red', linestyle='--', label='SL')
    plt.title(symbol)
    plt.legend()
    path = f"{symbol.replace('/', '_')}_trade.png"
    plt.savefig(path)
    plt.close()
    return path
