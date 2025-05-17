import ccxt
import pandas as pd
import numpy as np
from datetime import datetime
import matplotlib.pyplot as plt
import matplotlib.dates as mdates
import os

exchange = ccxt.kucoin()
markets = exchange.load_markets()
symbols = [s for s in markets if "USDT:USDT" in s and "PERP" in s]

def get_ohlcv(symbol, timeframe='1h', limit=200):
    try:
        data = exchange.fetch_ohlcv(symbol, timeframe=timeframe, limit=limit)
        df = pd.DataFrame(data, columns=['timestamp', 'open', 'high', 'low', 'close', 'volume'])
        return df
    except:
        return None

def detect_ote_fvg_zone(df):
    high = df['high'].iloc[-30:].max()
    low = df['low'].iloc[-30:].min()
    ote_high = low + 0.618 * (high - low)
    ote_low = low + 0.786 * (high - low)

    fvg_low, fvg_high = None, None
    for i in range(-20, -5):
        if df['low'].iloc[i] > df['high'].iloc[i - 1]:
            fvg_low = df['high'].iloc[i - 1]
            fvg_high = df['low'].iloc[i]
            break
    if fvg_low is None:
        return None, None

    return (ote_low, ote_high), (fvg_low, fvg_high)

def calculate_dynamic_sl_tp(df, entry):
    df['tr'] = df[['high', 'low', 'close']].max(axis=1) - df[['high', 'low', 'close']].min(axis=1)
    atr = df['tr'].rolling(window=14).mean().iloc[-1]
    sl = round(entry - 1.5 * atr, 6)
    tp = round(entry + 2.5 * atr, 6)
    return sl, tp

def is_bos_valid(df):
    recent_high = df['high'].iloc[-5:-1].max()
    current_close = df['close'].iloc[-1]
    return current_close > recent_high

def rsi(series, period=14):
    delta = series.diff()
    gain = delta.where(delta > 0, 0).rolling(window=period).mean()
    loss = -delta.where(delta < 0, 0).rolling(window=period).mean()
    rs = gain / loss
    return 100 - (100 / (1 + rs))

def macd(series, short=12, long=26, signal=9):
    ema_short = series.ewm(span=short, adjust=False).mean()
    ema_long = series.ewm(span=long, adjust=False).mean()
    macd_line = ema_short - ema_long
    signal_line = macd_line.ewm(span=signal, adjust=False).mean()
    return macd_line, signal_line

def is_btc_favorable():
    try:
        btc_df = get_ohlcv("BTC/USDT:USDT", '1h', 100)
        btc_df['rsi'] = rsi(btc_df['close'])
        btc_df['macd'], btc_df['signal'] = macd(btc_df['close'])
        return btc_df['rsi'].iloc[-1] > 50 and btc_df['macd'].iloc[-1] > btc_df['signal'].iloc[-1]
    except:
        return True

def already_sent(signal_id):
    if not os.path.exists("sent_signals.csv"):
        return False
    df = pd.read_csv("sent_signals.csv")
    return signal_id in df['id'].values

def save_sent_signal(signal_id):
    now = datetime.utcnow().isoformat()
    row = pd.DataFrame([[signal_id, now]], columns=['id', 'timestamp'])
    if not os.path.exists("sent_signals.csv"):
        row.to_csv("sent_signals.csv", index=False)
    else:
        row.to_csv("sent_signals.csv", mode='a', header=False, index=False)

def generate_chart(df, symbol, ote_zone, fvg_zone, entry, sl, tp, direction):
    df = df.copy().tail(100)
    df['timestamp'] = pd.to_datetime(df['timestamp'], unit='ms')
    df.set_index('timestamp', inplace=True)

    fig, ax = plt.subplots(figsize=(10, 5))
    width = 0.0005 * (df.index[-1] - df.index[0]).total_seconds()

    for i in range(len(df)):
        color = 'green' if df['close'].iloc[i] >= df['open'].iloc[i] else 'red'
        ax.plot([df.index[i], df.index[i]], [df['low'].iloc[i], df['high'].iloc[i]], color=color, linewidth=0.5)
        ax.add_patch(plt.Rectangle(
            (df.index[i], min(df['open'].iloc[i], df['close'].iloc[i])),
            width,
            abs(df['close'].iloc[i] - df['open'].iloc[i]),
            color=color
        ))

    ax.axhspan(ote_zone[1], ote_zone[0], color='blue', alpha=0.2, label='OTE')
    ax.axhspan(fvg_zone[1], fvg_zone[0], color='orange', alpha=0.2, label='FVG')
    ax.axhline(entry, color='blue', linestyle='--', linewidth=1)
    ax.axhline(sl, color='red', linestyle='--', linewidth=1)
    ax.axhline(tp, color='green', linestyle='--', linewidth=1)

    y_start = entry
    y_end = tp if direction == "LONG" else sl
    ax.annotate('', xy=(df.index[-1], y_end), xytext=(df.index[-1], y_start),
                arrowprops=dict(facecolor='blue', shrink=0.05, width=2, headwidth=8))

    ax.set_title(f'{symbol} - {direction}')
    ax.legend()
    ax.xaxis.set_major_formatter(mdates.DateFormatter('%m-%d %H:%M'))
    plt.xticks(rotation=45)
    plt.tight_layout()

    path = f"chart_{symbol.replace('/', '_')}_{direction}.png"
    plt.savefig(path)
    plt.close()
    return path

async def scan_and_send_signals(bot, chat_id):
    for symbol in symbols:
        try:
            df = get_ohlcv(symbol, timeframe='1h', limit=200)
            if df is None or len(df) < 100:
                continue

            last_close = df['close'].iloc[-1]
            volume = df['volume'].iloc[-1]
            prev_volume = df['volume'].iloc[-2]

            if not is_bos_valid(df):
                continue
            if volume < prev_volume:
                continue
            if not is_btc_favorable():
                continue

            ote_zone, fvg_zone = detect_ote_fvg_zone(df)
            if ote_zone is None or fvg_zone is None:
                continue

            close_confirmed = last_close > max(ote_zone[0], fvg_zone[0])
            entry = round((ote_zone[0] + fvg_zone[0]) / 2, 6)
            sl, tp = calculate_dynamic_sl_tp(df, entry)

            direction = "LONG"
            signal_type = "CONFIRMÉ" if close_confirmed else "ANTICIPÉ"
            unique_id = f"{symbol}-{signal_type}"

            if already_sent(unique_id):
                continue

            chart_path = generate_chart(df, symbol, ote_zone, fvg_zone, entry, sl, tp, direction)

            message = f"""
{symbol} - Signal {signal_type} ({direction})

🔵 Entrée idéale : {entry}
🛑 SL : {sl}
🎯 TP : {tp}
📈 Signal {'confirmé ✅' if signal_type == 'CONFIRMÉ' else 'anticipé ⏳'}
"""

            await bot.send_photo(chat_id=chat_id, photo=open(chart_path, 'rb'), caption=message)
            save_sent_signal(unique_id)

        except Exception as e:
            print(f"[Erreur {symbol}] {e}")
