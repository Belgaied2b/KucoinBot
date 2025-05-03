import pandas_ta as ta

def analyze_market(symbol, df):
    df["rsi"] = ta.rsi(df["close"], length=14)
    macd = ta.macd(df["close"])
    df["macd"] = macd["MACD_12_26_9"]
    df["signal"] = macd["MACDs_12_26_9"]

    rsi = df["rsi"].iloc[-1]
    macd_val = df["macd"].iloc[-1]
    signal_val = df["signal"].iloc[-1]

    # Conditions swing
    if 40 < rsi < 60 and macd_val > signal_val:
        entry = round(df["close"].iloc[-1] * 0.995, 4)
        tp = round(entry * 1.03, 4)
        sl = round(entry * 0.97, 4)
        return {
            "message": f"<b>{symbol}</b>\n🎯 Entrée: <code>{entry}</code>\n📈 TP: <code>{tp}</code>\n🛡 SL: <code>{sl}</code>",
            "graph_path": generate_trade_graph(symbol, df, entry, tp, sl)
        }
    return None
