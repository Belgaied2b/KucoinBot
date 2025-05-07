import matplotlib.pyplot as plt

def plot_signal_graph(df, entry, sl, tp, direction):
    try:
        # Sélection des 50 dernières bougies pour lisibilité
        df = df[-50:]
        fig, ax = plt.subplots(figsize=(10, 5))

        # Affichage du prix
        ax.plot(df['close'].values, label='Close', linewidth=2)

        # Lignes horizontales pour Entry, SL et TP
        ax.axhline(entry, color='blue', linestyle='--', label=f'Entry @ {round(entry, 2)}')
        ax.axhline(sl, color='red', linestyle='--', label=f'SL @ {round(sl, 2)}')
        ax.axhline(tp, color='green', linestyle='--', label=f'TP @ {round(tp, 2)}')

        # Titre du graphique
        ax.set_title(f"Signal {direction.upper()} détecté")
        ax.set_xlabel("Bougies (4H)")
        ax.set_ylabel("Prix")
        ax.legend()
        ax.grid(True)
        plt.tight_layout()

        return fig

    except Exception as e:
        print(f"Erreur lors de la génération du graphique : {e}")
        return None
