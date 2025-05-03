import os
import sys
import pandas as pd
import pandas_ta as ta
import httpx
from kucoin_utils import get_kucoin_perps, fetch_klines

# --- Définitions techniques ---
def calculate_rsi(series, length=14):
    delta = series.diff()
    gain = delta.where(delta > 0, 0.0)
    loss = -delta.where(delta < 0, 0.0)
    avg_gain = gain.rolling(window=length).mean()
    avg_loss = loss.rolling(window=length).mean()
    rs = avg_gain / avg_loss
    return 100 - 100 / (1 + rs)

def calculate_macd(series, fast=12, slow=26, signal_len=9):
    ema_fast = series.ewm(span=fast, adjust=False).mean()
    ema_slow = series.ewm(span=slow, adjust=False).mean()
    macd_line = ema_fast - ema_slow
    signal_line = macd_line.ewm(span=signal_len, adjust=False).mean()
    return macd_line, signal_line

def is_in_ote_zone(entry, low, high):
    fib618 = low + 0.618 * (high - low)
    fib786 = low + 0.786 * (high - low)
    return fib786 <= entry <= fib618

def detect_fvg(df):
    zones = []
    for i in range(2, len(df)):
        if df['high'].iat[i-2] < df['low'].iat[i]:
            zones.append((df['high'].iat[i-2], df['low'].iat[i]))
    return zones

# --- Collecte et calculs ---
symbols = get_kucoin_perps()[:50]
records = []

for symbol in symbols:
    try:
        df = fetch_klines(symbol)
        close = df['close']
        # RSI et MACD
        rsi = calculate_rsi(close).iloc[-1]
        macd_line, signal_line = calculate_macd(close)
        macd_val = macd_line.iloc[-1]
        sig_val  = signal_line.iloc[-1]
        # Swing pour entry
        swing_low  = df['low'].iloc[-21:-1].min()
        swing_high = df['high'].iloc[-21:-1].max()
        entry = swing_low + 0.5 * (swing_high - swing_low)
        # Tests des filtres
        rsi_ok   = 40 <= rsi <= 60
        macd_ok  = macd_val >= sig_val - 0.001
        ote_ok   = is_in_ote_zone(entry, swing_low, swing_high)
        fvg_ok   = any(l <= entry <= h for l, h in detect_fvg(df))
        signal   = (rsi_ok and macd_ok and ote_ok and fvg_ok)
        records.append({
            'symbol': symbol,
            'last_rsi': rsi,
            'macd_minus_signal': macd_val - sig_val,
            'rsi_ok': rsi_ok,
            'macd_ok': macd_ok,
            'ote_ok': ote_ok,
            'fvg_ok': fvg_ok,
            'signal': signal
        })
    except Exception as e:
        print(f"⛔ Erreur pour {symbol}: {e}")

# --- Résultats ---
df_stats = pd.DataFrame(records)
df_means = df_stats.groupby('signal').mean().reset_index()

print("\nValeurs et filtres par symbole :")
print(df_stats)

print("\nMoyennes (Signal vs No-Signal) :")
print(df_means)
