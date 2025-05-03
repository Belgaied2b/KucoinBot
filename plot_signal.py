import matplotlib.pyplot as plt
from io import BytesIO

def generate_trade_graph(df, entry, sl, tp, symbol):
    plt.figure(figsize=(10, 5))
    plt.plot(df['close'], label='Close Price')
    plt.axhline(entry, color='blue', linestyle='--', label='Entrée')
    plt.axhline(sl, color='red', linestyle='--', label='SL')
    plt.axhline(tp, color='green', linestyle='--', label='TP')
    plt.title(f"Signal sur {symbol}")
    plt.legend()

    buffer = BytesIO()
    plt.savefig(buffer, format='png')
    buffer.seek(0)
    plt.close()
    return buffer
