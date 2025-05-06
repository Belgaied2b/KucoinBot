# multi_tf.py
import pandas as pd
from signal_analysis import analyze_market

def confirm_multi_tf(symbol: str, df_low: pd.DataFrame, df_high: pd.DataFrame, side: str) -> bool:
    """
    Vérifie qu'il y a bien un signal (long/short) sur le timeframe supérieur
    avant de valider l'entrée sur le bas timeframe.
    """
    res = analyze_market(symbol, df_high, side=side)
    return res is not None
