import matplotlib.pyplot as plt
import pandas as pd

def generate_trade_graph(df, signal_type, entry_price, sl, tp, symbol):
    try:
        fig, ax = plt.subplots(figsize=(10, 5))
        df['close'].plot(ax=ax, label='Prix', linewidth=1)

        ax.axhline(entry_price, color='blue', linestyle='--', label='Entrée')
        ax.axhline(sl, color='red', linestyle='--', label='Stop Loss')
        ax.axhline(tp, color='green', linestyle='--', label='Take Profit')

        ax.set_title(f"Signal {signal_type.upper()} pour {symbol}")
        ax.set_xlabel("Temps")
        ax.set_ylabel("Prix")
        ax.legend()

        filename = f"signal_{symbol.replace('/', '_')}.png"
        plt.tight_layout()
        plt.savefig(filename)
        plt.close()
        return filename
    except Exception as e:
        print(f"Erreur lors de la génération du graphique : {e}")
        return None
