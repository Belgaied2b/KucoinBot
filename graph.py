import matplotlib.pyplot as plt
import matplotlib.patches as patches

def plot_signal_graph(df, entry, sl, tp, direction, show_ote=True, show_fvg=True):
    try:
        df = df[-50:]
        fig, ax = plt.subplots(figsize=(10, 5))

        # Courbe des prix
        ax.plot(df['close'].values, label='Prix (close)', linewidth=2)

        # Zone OTE
        if show_ote:
            high = df['high'].rolling(20).max().iloc[-2]
            low = df['low'].rolling(20).min().iloc[-2]
            if direction == "long":
                fib618 = low + 0.618 * (high - low)
                fib786 = low + 0.786 * (high - low)
                ax.axhspan(fib618, fib786, color='blue', alpha=0.2, label='Zone OTE')
            else:
                fib618 = high - 0.618 * (high - low)
                fib786 = high - 0.786 * (high - low)
                ax.axhspan(fib786, fib618, color='red', alpha=0.2, label='Zone OTE')

        # Zone FVG (approximative)
        if show_fvg:
            fvg_low = df['low'].min() - 5
            fvg_high = df['high'].max() + 5
            ax.axhspan(fvg_low, fvg_high, color='orange', alpha=0.1, label='Zone FVG')

        # Entrée idéale
        ax.axhline(entry, color='blue', linestyle='--', label=f'Entrée idéale ({round(entry, 2)})')

        # SL et TP (uniquement si signal confirmé)
        if sl and tp:
            ax.axhline(sl, color='red', linestyle='--', label=f'SL ({round(sl, 2)})')
            ax.axhline(tp, color='green', linestyle='--', label=f'TP ({round(tp, 2)})')

        # Direction visuelle
        arrow_y = entry * (1.01 if direction == "long" else 0.99)
        ax.annotate('⬆️' if direction == "long" else '⬇️',
                    xy=(len(df) - 1, arrow_y),
                    fontsize=14)

        ax.set_title(f"Signal {direction.upper()} {'ANTICIPÉ' if sl is None else 'CONFIRMÉ'}")
        ax.set_xlabel("Bougies 4H")
        ax.set_ylabel("Prix")
        ax.legend()
        ax.grid(True)
        plt.tight_layout()

        return fig

    except Exception as e:
        print(f"Erreur lors du graphique : {e}")
        return None
