# indicators.py

import pandas as pd
import pandas_ta as ta

def compute_rsi(series: pd.Series, length: int = 14) -> pd.Series:
    """
    Calcule le RSI sur la série de prix.
    """
    # pandas_ta renvoie un Series nommé RSI_{length}
    rsi = ta.rsi(series, length=length)
    return rsi

def compute_macd(series: pd.Series,
                 fast: int = 12,
                 slow: int = 26,
                 signal_len: int = 9):
    """
    Calcule la MACD, sa ligne de signal (et l'histogramme en option).
    Renvoie (macd_line, signal_line, macd_hist).
    """
    df = ta.macd(series, fast=fast, slow=slow, signal=signal_len)
    macd_col    = f"MACD_{fast}_{slow}_{signal_len}"
    signal_col  = f"MACDs_{fast}_{slow}_{signal_len}"
    hist_col    = f"MACDh_{fast}_{slow}_{signal_len}"
    macd_line   = df[macd_col]
    signal_line = df[signal_col]
    macd_hist   = df[hist_col] if hist_col in df else None
    return macd_line, signal_line, macd_hist

def compute_atr(high: pd.Series,
                low:  pd.Series,
                close:pd.Series,
                length: int = 14) -> pd.Series:
    """
    Calcule l'Average True Range (ATR) à partir des High/Low/Close.
    """
    atr = ta.atr(high, low, close, length=length)
    return atr
