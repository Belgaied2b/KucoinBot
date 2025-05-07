import matplotlib.pyplot as plt

def plot_signal_graph(df, entry, sl, tp, direction, show_ote=True, show_fvg=True):
    try:
        # ⚠️ Utilise suffisamment de bougies récentes
        df = df[-100:]
        prices = df['close'].values

        fig, ax = plt.subplots(figsize=(10, 5))
        ax.plot(prices, label='Prix 4H', linewidth=2)

        # OTE zone (bleue ou rouge)
        high = df['high'].rolling(20).max().iloc[-2]
        low = df['low'].rolling(20).min().iloc[-2]

        if show_ote:
            if direction == "long":
                fib618 = low + 0.618 * (high - low)
                fib786 = low + 0.786 * (high - low)
                ax.axhspan(fib618, fib786, color='blue', alpha=0.2, label='Zone OTE')
            else:
                fib618 = high - 0.618 * (high - low)
                fib786 = high - 0.786 * (high - low)
                ax.axhspan(fib786, fib618, color='red', alpha=0.2, label='Zone OTE')

        # FVG (approximatif)
        if show_fvg:
            fvg_low = df['low'].min() - 0.003 * df['low'].min()
            fvg_high = df['high'].max() + 0.003 * df['high'].max()
            ax.axhspan(fvg_low, fvg_high, color='orange', alpha=0.1, label='Zone FVG')

        # Entrée
        if entry is not None:
            ax.axhline(entry, color='blue', linestyle='--', label=f'Entrée ({entry:.6f})')

        # SL
        if sl is not None:
            ax.axhline(sl, color='red', linestyle='--', label=f'SL ({sl:.6f})')

        # TP
        if tp is not None:
            ax.axhline(tp, color='green', linestyle='--', label=f'TP ({tp:.6f})')

        # Flèche direction
        arrow_y = entry if entry else prices[-1]
        ax.annotate('⬆️' if direction == "long" else '⬇️',
                    xy=(len(prices) - 1, arrow_y),
                    fontsize=14)

        ax.set_title(f"Signal {direction.upper()}")
        ax.set_xlabel("Bougies 4H")
        ax.set_ylabel("Prix")
        ax.grid(True)
        ax.legend()
        plt.tight_layout()

        return fig

    except Exception as e:
        print(f"[GRAPH ERROR] {e}")
        return None
