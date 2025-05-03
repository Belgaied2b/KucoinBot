# graph.py

import matplotlib.pyplot as plt
import os

def generate_trade_graph(df, symbol, entry_price, stop_loss, take_profit, direction):
    try:
        fig, ax = plt.subplots(figsize=(10, 5))
        ax.plot(df["close"], label="Price", linewidth=1.5)
        ax.axhline(entry_price, color="blue", linestyle="--", label="Entry")
        ax.axhline(stop_loss, color="red", linestyle="--", label="Stop Loss")
        ax.axhline(take_profit, color="green", linestyle="--", label="Take Profit")

        if direction == "LONG":
            ax.fill_between(df.index, stop_loss, take_profit, where=(df["close"] > stop_loss), color='green', alpha=0.1)
        else:
            ax.fill_between(df.index, take_profit, stop_loss, where=(df["close"] < stop_loss), color='red', alpha=0.1)

        ax.set_title(f"{symbol} Trade Setup ({direction})")
        ax.set_ylabel("Price")
        ax.set_xlabel("Candle")
        ax.legend()
        ax.grid(True)

        path = f"{symbol.replace('/', '_')}_signal.png"
        plt.tight_layout()
        plt.savefig(path)
        plt.close()

        return path
    except Exception as e:
        print(f"Erreur dans generate_trade_graph: {e}")
        return None
