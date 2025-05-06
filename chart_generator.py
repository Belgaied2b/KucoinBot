# chart_generator.py
import matplotlib.pyplot as plt
import io
import base64

def generate_signal_chart(df, entry_min, entry_max, symbol: str, timeframe: str) -> str:
    """
    Trace les 50 dernières bougies + zones Fib, renvoie un PNG encodé Base64.
    """
    fig, ax = plt.subplots()
    df_last = df.iloc[-50:]
    ax.plot(df_last.index, df_last['close'], label='Close')
    ax.axhline(entry_min, linestyle='--', label='Entry Min')
    ax.axhline(entry_max, linestyle='--', label='Entry Max')
    ax.set_title(f"{symbol}  {timeframe} zone d'entrée")
    ax.legend()
    buf = io.BytesIO()
    fig.savefig(buf, format='png')
    buf.seek(0)
    img_b64 = base64.b64encode(buf.read()).decode()
    buf.close()
    plt.close(fig)
    return img_b64
