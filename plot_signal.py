import matplotlib.pyplot as plt
from io import BytesIO

def generate_trade_graph(symbol, df, signal):
    plt.figure(figsize=(10, 5))
    plt.plot(df["close"], label="Prix de clôture")
    plt.axhline(signal["entry"], color="blue", linestyle="--", label=f"Entrée {signal['entry']}")
    plt.axhline(signal["tp"], color="green", linestyle="--", label=f"TP {signal['tp']}")
    plt.axhline(signal["sl"], color="red", linestyle="--", label=f"SL {signal['sl']}")
    plt.title(f"{symbol} - Signal LONG")
    plt.legend()
    buf = BytesIO()
    plt.savefig(buf, format="png")
    buf.seek(0)
    plt.close()
    return buf
