import numpy as np
from indicators import (
    detect_ote_zone, detect_fvg, is_above_ma200, is_below_ma200,
    calculate_atr, calculate_macd_histogram
)

def confirm_with_4h(df_4h, direction):
    try:
        ma200 = df_4h['close'].rolling(window=200).mean()
        last_close = df_4h['close'].iloc[-1]
        last_open = df_4h['open'].iloc[-1]
        last_volume = df_4h['volume'].iloc[-1]
        avg_volume = df_4h['volume'].mean()

        is_bull = last_close > last_open
        is_bear = last_close < last_open
        strong_vol = last_volume > avg_volume * 1.2
        above_ma = last_close > ma200.iloc[-1]
        below_ma = last_close < ma200.iloc[-1]

        if direction == 'long':
            return is_bull and strong_vol and above_ma
        else:
            return is_bear and strong_vol and below_ma
    except:
        return False

def analyze_signal(df_dict, symbol, direction):
    df = df_dict["1h"]
    df_4h = df_dict["4h"]
    df.name = symbol

    if df is None or df.empty or 'timestamp' not in df.columns:
        return None

    # MACD momentum
    macd_hist = calculate_macd_histogram(df)
    if direction == 'long' and macd_hist.iloc[-1] < 0:
        return None
    if direction == 'short' and macd_hist.iloc[-1] > 0:
        return None

    # Zone OTE
    ote_zone = detect_ote_zone(df, direction)
    if ote_zone is None:
        return None

    last_price = df['close'].iloc[-1]
    if not (ote_zone[0] <= last_price <= ote_zone[1]):
        return None

    # FVG directionnel
    fvg_zone = detect_fvg(df, direction)
    if fvg_zone is None:
        return None

    # MA200
    if direction == 'long' and not is_above_ma200(df):
        return None
    if direction == 'short' and not is_below_ma200(df):
        return None

    # Confirmation 4H obligatoire
    if not confirm_with_4h(df_4h, direction):
        return None

    # ATR / SL / TP
    atr = calculate_atr(df)
    if atr is None or atr == 0:
        return None

    if direction == 'long':
        sl = df['low'].iloc[-1] - atr
        tp = last_price + (last_price - sl) * 2
    else:
        sl = df['high'].iloc[-1] + atr
        tp = last_price - (sl - last_price) * 2

    # Résultat
    return {
        "symbol": symbol,
        "direction": direction,
        "entry": last_price,
        "sl": round(sl, 4),
        "tp": round(tp, 4),
        "ote_zone": ote_zone,
        "fvg_zone": fvg_zone
    }
