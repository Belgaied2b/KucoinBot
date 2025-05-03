#!/usr/bin/env python3
import logging
import pandas as pd
import pandas_ta as ta
from kucoin_utils import get_kucoin_perps, fetch_klines

def calculate_rsi(series, length=14):
    delta = series.diff()
    gain  = delta.where(delta > 0, 0.0)
    loss  = -delta.where(delta < 0, 0.0)
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

def compute_stats(limit=50):
    symbols = get_kucoin_perps()[:limit]
    records = []
    for sym in symbols:
        try:
            df = fetch_klines(sym)
            close = df['close']

            # RSI & MACD
            last_rsi = calculate_rsi(close).iloc[-1]
            macd_line, signal_line = calculate_macd(close)
            last_macd   = macd_line.iloc[-1]
            last_signal = signal_line.iloc[-1]

            # Swing pour entry
            swing_low  = df['low'].iloc[-21:-1].min()
            swing_high = df['high'].iloc[-21:-1].max()
            entry      = swing_low + 0.5 * (swing_high - swing_low)

            # Filtres
            rsi_ok  = 40 <= last_rsi <= 60
            macd_ok = last_macd >= (last_signal - 0.001)
            ote_ok  = is_in_ote_zone(entry, swing_low, swing_high)
            fvg_ok  = any(l <= entry <= h for l, h in detect_fvg(df))
            signal  = all((rsi_ok, macd_ok, ote_ok, fvg_ok))

            records.append({
                'symbol': sym,
                'last_rsi': round(last_rsi, 2),
                'macd_minus_signal': round(last_macd - last_signal, 6),
                'rsi_ok': rsi_ok,
                'macd_ok': macd_ok,
                'ote_ok': ote_ok,
                'fvg_ok': fvg_ok,
                'signal': signal
            })
        except Exception as e:
            records.append({
                'symbol': sym,
                'error': str(e)
            })

    df_stats = pd.DataFrame(records)
    # Ne garder pour la moyenne que les colonnes numériques
    df_numeric = df_stats.drop(columns=['symbol', 'error'], errors='ignore')
    df_means   = df_numeric.groupby('signal').mean().reset_index()

    return df_stats, df_means

def main():
    # Pour ne pas être noyé dans les INFO de l’API
    logging.basicConfig(level=logging.WARNING)
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("kucoin_utils").setLevel(logging.WARNING)

    print("\n🔎 Lancement de l'analyse statistique (console only)\n")
    df_stats, df_means = compute_stats(limit=50)

    print("🔢 Détail par symbole :")
    print(df_stats.to_string(index=False))

    print("\n📊 Moyennes (Signal vs No-Signal) :")
    print(df_means.to_string(index=False))

if __name__ == '__main__':
    main()
