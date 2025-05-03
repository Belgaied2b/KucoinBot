# plot_signal.py

import matplotlib.pyplot as plt
import io

def generate_trade_graph(df, signal):
    try:
        fig, ax = plt.subplots(figsize=(10, 5))
        df["close"].plot(ax=ax, label="Prix", linewidth=1)

        ax.axhline(signal["entry"], color="blue", linestyle="--", label="Entrée")
        ax.axhline(signal["tp"], color="green", linestyle="--", label="TP")
        ax.axhline(signal["sl"], color="red", linestyle="--", label="SL")

        ax.set_title(f"{signal['symbol']} - {signal['side']}")
        ax.set_ylabel("Prix")
        ax.legend()

        buffer = io.BytesIO()
        plt.savefig(buffer, format="png")
        buffer.seek(0)
        plt.close()
        return buffer
    except Exception as e:
        print(f"Erreur création graphique: {e}")
        return None
