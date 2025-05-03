import pandas as pd
import pandas_ta as ta

def analyze_symbol(symbol, df):
    try:
        # RSI
        df["rsi"] = ta.rsi(df["close"], length=14)

        # MACD
        macd = ta.macd(df["close"])
        df["macd"] = macd["MACD_12_26_9"]
        df["macd_signal"] = macd["MACDs_12_26_9"]

        # Volume boost
        volume_mean = df["volume"].rolling(20).mean()
        df["volume_boost"] = df["volume"] > volume_mean * 1.5

        # Fibonacci
        recent = df.tail(30)
        low = recent["low"].min()
        high = recent["high"].max()
        level_0 = low
        level_50 = low + 0.5 * (high - low)
        level_100 = high
        fib_zone = (level_0, level_50, level_100)

        last = df.iloc[-1]
        rsi = last["rsi"]
        macd_val = last["macd"]
        macd_signal = last["macd_signal"]
        price = last["close"]
        volume_ok = last["volume_boost"]

        # Conditions de signal
        if (
            40 < rsi < 60 and
            macd_val > macd_signal and
            fib_zone[0] < price < fib_zone[2] and
            volume_ok
        ):
            entry = round(price * 0.995, 4)  # petite entrée en dessous
            sl = round(recent["low"].min() * 0.995, 4)
            tp = round(entry * 1.03, 4)
            return {
                "message": f"💥 Signal LONG détecté sur {symbol}\n🎯 Entrée: {entry}\n📈 TP: {tp}\n🛑 SL: {sl}",
                "entry": entry,
                "sl": sl,
                "tp": tp,
                "direction": "long"
            }

        elif (
            40 < rsi < 60 and
            macd_val < macd_signal and
            fib_zone[0] < price < fib_zone[2] and
            volume_ok
        ):
            entry = round(price * 1.005, 4)  # entrée au-dessus
            sl = round(recent["high"].max() * 1.005, 4)
            tp = round(entry * 0.97, 4)
            return {
                "message": f"💥 Signal SHORT détecté sur {symbol}\n🎯 Entrée: {entry}\n📉 TP: {tp}\n🛑 SL: {sl}",
                "entry": entry,
                "sl": sl,
                "tp": tp,
                "direction": "short"
            }

        return None

    except Exception as e:
        print(f"Erreur dans l'analyse de {symbol}: {e}")
        return None
