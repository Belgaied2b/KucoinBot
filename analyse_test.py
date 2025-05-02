# analyse_test.py

import ccxt
import pandas as pd
import pandas_ta as ta

async def run_test_analysis():
    print("🚀 Début du scan test")
    exchange = ccxt.kucoinfutures()
    markets = exchange.load_markets()
    symbols = [s for s in markets if s.endswith(':USDTM')]

    def fetch_ohlcv(symbol, timeframe="4h", limit=100):
        try:
            ohlcv = exchange.fetch_ohlcv(symbol, timeframe=timeframe, limit=limit)
            df = pd.DataFrame(ohlcv, columns=["timestamp", "open", "high", "low", "close", "volume"])
            df["timestamp"] = pd.to_datetime(df["timestamp"], unit="ms")
            return df
        except Exception as e:
            print(f"❌ Erreur fetch {symbol}: {e}")
            return None

    def analyze(symbol, df):
        df["rsi"] = ta.rsi(df["close"], length=14)
        macd = ta.macd(df["close"])
        df["macd"] = macd["MACD_12_26_9"]
        df["macd_signal"] = macd["MACDs_12_26_9"]
        last = df.dropna().iloc[-1]

        print(f"🔎 {symbol.replace(':USDTM','')}: RSI={last['rsi']:.2f} | MACD={last['macd']:.5f} | Signal={last['macd_signal']:.5f}")

        if last["rsi"] < 40 and last["macd"] > last["macd_signal"]:
            return "LONG"
        elif last["rsi"] > 60 and last["macd"] < last["macd_signal"]:
            return "SHORT"
        return None

    results = []

    for symbol in symbols[:30]:
        print(f"📊 Analyse de {symbol}")
        df = fetch_ohlcv(symbol)
        if df is not None:
            signal = analyze(symbol, df)
            if signal:
                results.append((symbol.replace(":USDTM", ""), signal))

    print("✅ Scan terminé")
    return results
