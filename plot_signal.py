import matplotlib.pyplot as plt
from io import BytesIO

def generate_trade_graph(symbol, df, signal):
    plt.figure(figsize=(10, 5))
    plt.plot(df["close"], label="Close Price")
    plt.axhline(signal["entry"], color="blue", linestyle="--", label="Entrée")
    plt.axhline(signal["tp"], color="green", linestyle="--", label="TP")
    plt.axhline(signal["sl"], color="red", linestyle="--", label="SL")
    plt.title(f"{symbol} - Signal LONG")
    plt.legend()
    buf = BytesIO()
    plt.savefig(buf, format="png")
    buf.seek(0)
    plt.close()
    return buf
