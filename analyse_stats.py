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
    macd_line   = ema_fast - ema_slow
    signal_line = macd_line.ewm(span=signal_len, adjust=False).mean()
    return macd_line, signal_line

def is_in_ote_zone(entry, low, high):
    fib618 = low + 0.618 * (high - low)
    fib786 = low + 0.786 * (high - low)
    return (fib618 <= entry <= fib786), fib618, fib786

def detect_fvg(df):
    zones = []
    for i in range(2, len(df)):
        h2 = df['high'].iat[i-2]
        l0 = df['low'].iat[i]
        if h2 < l0:
            zones.append((h2, l0))
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

            # Swing (20 bougies avant la dernière)
            swing_low  = df['low'].iloc[-21:-1].min()
            swing_high = df['high'].iloc[-21:-1].max()

            # === ENTRÉE AU FIBO 61.8% ===
            entry = swing_low + 0.618 * (swing_high - swing_low)

            # OTE
            ote_ok, fib618, fib786 = is_in_ote_zone(entry, swing_low, swing_high)

            # FVG
            fvg_zones = detect_fvg(df)
            matching = next(((l,h) for l,h in fvg_zones if l <= entry <= h), None)
            fvg_ok = matching is not None
            fvg_low, fvg_high = matching if matching else (None, None)

            # Filtres
            rsi_ok  = 40 <= last_rsi <= 60
            macd_ok = last_macd >= (last_signal - 0.001)
            signal  = all((rsi_ok, macd_ok, ote_ok, fvg_ok))

            records.append({
                'symbol':         sym,
                'last_rsi':       round(last_rsi,   2),
                'last_macd':      round(last_macd,   6),
                'last_signal':    round(last_signal, 6),
                'macd_minus_sig': round(last_macd - last_signal, 6),
                'swing_low':      round(swing_low,   6),
                'swing_high':     round(swing_high,  6),
                'entry':          round(entry,       6),
                'ote_low':        round(fib618,      6),
                'ote_high':       round(fib786,      6),
                'fvg_low':        round(fvg_low,     6) if fvg_low else None,
                'fvg_high':       round(fvg_high,    6) if fvg_high else None,
                'rsi_ok':         rsi_ok,
                'macd_ok':        macd_ok,
                'ote_ok':         ote_ok,
                'fvg_ok':         fvg_ok,
                'signal':         signal
            })
        except Exception as e:
            records.append({'symbol': sym, 'error': str(e)})

    df_stats = pd.DataFrame(records)
    # Ne garder que les colonnes numériques pour les moyennes
    df_numeric = df_stats.drop(columns=['symbol','error'], errors='ignore')
    df_means   = df_numeric.groupby('signal').mean().reset_index()

    return df_stats, df_means

def main():
    logging.basicConfig(level=logging.WARNING)
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("kucoin_utils").setLevel(logging.WARNING)

    print("\n🔎 Lancement de l'analyse statistique (console only)\n")
    df_stats, df_means = compute_stats(limit=50)

    print("🔢 Détail par symbole (RSI, MACD, Fibo, OTE, FVG) :")
    print(df_stats.to_string(index=False))

    print("\n📊 Moyennes (Signal vs No-Signal) :")
    print(df_means.to_string(index=False))

if __name__ == '__main__':
    main()
