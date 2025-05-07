### graph.py
python
import matplotlib.pyplot as plt

def plot_signal_graph(df, entry, sl, tp, direction):
    fig, ax = plt.subplots(figsize=(10, 5))
    df = df[-50:]
    ax.plot(df['close'].values, label='Close')
    ax.axhline(entry, color='blue', linestyle='--', label=f'Entry {round(entry, 2)}')
    ax.axhline(sl, color='red', linestyle='--', label=f'SL {round(sl, 2)}')
    ax.axhline(tp, color='green', linestyle='--', label=f'TP {round(tp, 2)}')
    ax.set_title(f"Signal {direction.upper()}")
    ax.legend()
    return fig
