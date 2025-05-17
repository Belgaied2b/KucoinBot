import ccxt
import pandas as pd
import numpy as np
import os
from datetime import datetime, timedelta

exchange = ccxt.kucoin()

# Récupère les données OHLCV
def get_ohlcv(symbol, timeframe='1h', limit=200):
    try:
        data = exchange.fetch_ohlcv(symbol, timeframe=timeframe, limit=limit)
        df = pd.DataFrame(data, columns=['timestamp', 'open', 'high', 'low', 'close', 'volume'])
        return df
    except:
        return None

# Détection zone OTE (entre fib 0.618 et 0.786) et FVG (gap entre bougies)
def detect_ote_fvg_zone(df):
    high = df['high'].iloc[-30:].max()
    low = df['low'].iloc[-30:].min()
    ote_high = low + 0.618 * (high - low)
    ote_low = low + 0.786 * (high - low)

    fvg_low = None
    fvg_high = None
    for i in range(-20, -5):
        if df['low'].iloc[i] > df['high'].iloc[i - 1]:
            fvg_low = df['high'].iloc[i - 1]
            fvg_high = df['low'].iloc[i]
            break
    if fvg_low is None:
        return None, None

    return (ote_low, ote_high), (fvg_low, fvg_high)

# SL/TP dynamiques basés sur ATR
def calculate_dynamic_sl_tp(df, entry):
    df['tr'] = df[['high', 'low', 'close']].max(axis=1) - df[['high', 'low', 'close']].min(axis=1)
    atr = df['tr'].rolling(window=14).mean().iloc[-1]
    sl = round(entry - 1.5 * atr, 6)
    tp = round(entry + 2.5 * atr, 6)
    return sl, tp

# Breakout de structure simple : plus haut récent cassé
def is_bos_valid(df):
    recent_high = df['high'].iloc[-5:-1].max()
    current_close = df['close'].iloc[-1]
    return current_close > recent_high

# BTC favorable : RSI et MACD alignés sur BTC/USDT perp
def is_btc_favorable():
    try:
        btc_df = get_ohlcv("BTC/USDT:USDT", '1h', 100)
        btc_df['rsi'] = rsi(btc_df['close'])
        btc_df['macd'] = macd(btc_df['close'])[0]
        btc_df['signal'] = macd(btc_df['close'])[1]
        return btc_df['rsi'].iloc[-1] > 50 and btc_df['macd'].iloc[-1] > btc_df['signal'].iloc[-1]
    except:
        return True  # fail-safe si données inaccessibles

# RSI
def rsi(series, period=14):
    delta = series.diff()
    gain = (delta.where(delta > 0, 0)).rolling(window=period).mean()
    loss = (-delta.where(delta < 0, 0)).rolling(window=period).mean()
    rs = gain / loss
    return 100 - (100 / (1 + rs))

# MACD
def macd(series, short=12, long=26, signal=9):
    ema_short = series.ewm(span=short, adjust=False).mean()
    ema_long = series.ewm(span=long, adjust=False).mean()
    macd_line = ema_short - ema_long
    signal_line = macd_line.ewm(span=signal, adjust=False).mean()
    return macd_line, signal_line

# Anti-doublons (CSV)
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
