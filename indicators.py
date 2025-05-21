# indicators.py

import pandas as pd

def compute_rsi(df: pd.DataFrame, period: int = 14) -> pd.Series:
    """
    Calcule le RSI (Relative Strength Index) sur la colonne 'close' de df.
    Stub : à implémenter.
    """
    # TODO: remplacer par l'implémentation réelle
    # Exemple minimal : renvoyer une série à NaN
    return pd.Series([float('nan')] * len(df), index=df.index)

def compute_macd(df: pd.DataFrame, fast: int = 12, slow: int = 26, signal: int = 9) -> pd.DataFrame:
    """
    Calcule le MACD sur la colonne 'close' de df.
    Renvoie un DataFrame avec colonnes ['macd', 'signal', 'histogram'].
    Stub : à implémenter.
    """
    # TODO: remplacer par l'implémentation réelle
    return pd.DataFrame({
        'macd':    [float('nan')] * len(df),
        'signal':  [float('nan')] * len(df),
        'histogram': [float('nan')] * len(df),
    }, index=df.index)

def compute_fvg(df: pd.DataFrame, window: int = 5) -> pd.DataFrame:
    """
    Calcule les Fair Value Gaps (FVG) sur df.
    Stub : à implémenter.
    """
    # TODO: remplacer par l'implémentation réelle
    return pd.DataFrame({
        'fvg_upper': [float('nan')] * len(df),
        'fvg_lower': [float('nan')] * len(df),
    }, index=df.index)

def compute_ote(df: pd.DataFrame, window: int = 5) -> pd.DataFrame:
    """
    Calcule l'Optimal Trade Entry (OTE) sur df.
    Stub : à implémenter.
    """
    # TODO: remplacer par l'implémentation réelle
    return pd.DataFrame({
        'ote_upper': [float('nan')] * len(df),
        'ote_lower': [float('nan')] * len(df),
    }, index=df.index)

def compute_atr(df: pd.DataFrame, period: int = 14) -> pd.Series:
    """
    Calcule l'Average True Range (ATR) sur df.
    Stub : à implémenter.
    """
    # TODO: remplacer par l'implémentation réelle
    return pd.Series([float('nan')] * len(df), index=df.index)

def find_pivots(df: pd.DataFrame, window: int = 5):
    """
    Détecte les pivots hauts et bas dans df.
    - window : nombre de barres avant et après pour comparer.
    Retourne deux listes d’indices : (highs, lows).
    """
    highs, lows = [], []
    for i in range(window, len(df) - window):
        # pivot haut
        if df['high'].iloc[i] == df['high'].iloc[i-window:i+window+1].max():
            highs.append(i)
        # pivot bas
        if df['low'].iloc[i] == df['low'].iloc[i-window:i+window+1].min():
            lows.append(i)
    return highs, lows
