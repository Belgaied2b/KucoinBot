import pandas as pd

def compute_rsi(prices, period: int = 14):
    # ... implémentation existante de compute_rsi ...
    pass


def compute_macd(prices, fast: int = 12, slow: int = 26, signal: int = 9):
    # ... implémentation existante de compute_macd ...
    pass


def compute_fvg(df: pd.DataFrame, direction: str):
    # ... implémentation existante de compute_fvg ...
    pass


def compute_ote(df: pd.DataFrame, direction: str):
    # ... implémentation existante de compute_ote ...
    pass


def compute_atr(df: pd.DataFrame, period: int = 14):
    # ... implémentation existante de compute_atr ...
    pass


def find_pivots(df: pd.DataFrame, window: int = 5):
    """
    Détecte les pivots hauts et bas dans une série de prix.
    - window: nombre de barres avant et après pour comparer.
    Retourne deux listes d’indices: highs, lows.
    """
    highs, lows = [], []
    for i in range(window, len(df) - window):
        # Pivot haut
        slice_high = df['high'].iloc[i - window: i + window + 1]
        if df['high'].iat[i] == slice_high.max():
            highs.append(i)
        # Pivot bas
        slice_low = df['low'].iloc[i - window: i + window + 1]
        if df['low'].iat[i] == slice_low.min():
            lows.append(i)
    return highs, lows
