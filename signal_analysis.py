import pandas as pd
import pandas_ta as ta
from plot_signal import generate_trade_graph

async def analyze_market(bot, symbol, df):
    df['rsi'] = ta.rsi(df['close'], length=14)
    macd = ta.macd(df['close'])
    if macd is not None:
        df['macd'] = macd['MACD_12_26_9']
        df['signal'] = macd['MACDs_12_26_9']
    else:
        return None

    if df['rsi'].iloc[-1] < 40 or df['rsi'].iloc[-1] > 60:
        return None

    if df['macd'].iloc[-1] > df['signal'].iloc[-1] and df['macd'].iloc[-2] < df['signal'].iloc[-2]:
        entry = df['close'].iloc[-1]
        sl = df['low'].iloc[-20:-1].min()  # SL sous support
        tp = entry * 1.03
        image = generate_trade_graph(df, entry, sl, tp, symbol)

        message = f"🔔 Signal LONG détecté sur {symbol}\n\n🎯 Entrée : {entry:.4f}\n🛡️ SL : {sl:.4f}\n💰 TP : {tp:.4f}"
        return image, message

    return None
